"""Deterministic risk features. Code computes the numbers; models interpret them.

Everything here works on real identifiers and stays inside the boundary.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher

from .bankdb import BankDB
from .models import Alert
from .policy import Policy


def activity_summary(
    db: BankDB,
    account_id: str,
    start: date,
    end: date,
    expected_monthly_turnover: float,
    policy: Policy,
) -> dict:
    rows = db.transactions(account_id, start, end)
    ins = [r for r in rows if r["direction"] == "in"]
    outs = [r for r in rows if r["direction"] == "out"]
    total_in = sum(r["amount"] for r in ins)
    total_out = sum(r["amount"] for r in outs)
    cash_in = sum(r["amount"] for r in ins if r["channel"] == "cash")

    low, high = policy.reporting_threshold * policy.structuring_band, policy.reporting_threshold
    band = [r["amount"] for r in ins if r["channel"] == "cash" and low <= r["amount"] < high]

    out_times = [(datetime.fromisoformat(o["booked_at"]), o["amount"]) for o in outs]
    passed = 0.0
    for r in ins:
        arrived = datetime.fromisoformat(r["booked_at"])
        left = sum(a for t, a in out_times if arrived < t <= arrived + timedelta(hours=48))
        passed += min(r["amount"], left)

    months: dict[str, dict] = defaultdict(lambda: {"in": 0.0, "out": 0.0, "count": 0})
    parties: dict[str, dict] = defaultdict(lambda: {"in": 0.0, "out": 0.0, "count": 0})
    for r in rows:
        month = months[r["booked_at"][:7]]
        month[r["direction"]] = round(month[r["direction"]] + r["amount"], 2)
        month["count"] += 1
        if r["counterparty_id"]:
            party = parties[r["counterparty_id"]]
            party[r["direction"]] = round(party[r["direction"]] + r["amount"], 2)
            party["count"] += 1
    top = sorted(parties.items(), key=lambda kv: (-(kv[1]["in"] + kv[1]["out"]), kv[0]))[:5]

    span_months = max(((end - start).days + 1) / 30.4, 1.0)
    return {
        "total_in": round(total_in, 2),
        "total_out": round(total_out, 2),
        "cash_in_share": round(cash_in / total_in, 2) if total_in else 0.0,
        "structuring": {"count": len(band), "total": round(sum(band), 2), "band": [low, high]},
        "pass_through_ratio": round(min(passed / total_in, 1.0), 2) if total_in else 0.0,
        "turnover_ratio": (
            round(total_in / span_months / expected_monthly_turnover, 2)
            if expected_monthly_turnover
            else None
        ),
        "distinct_senders": len({r["counterparty_id"] for r in ins if r["counterparty_id"]}),
        "by_month": dict(sorted(months.items())),
        "top_counterparties": [{"counterparty_id": k, **v} for k, v in top],
    }


def _norm(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(sorted(re.sub(r"[^a-z0-9 ]", " ", plain.lower()).split()))


def _compare(ours: str, theirs: str) -> str:
    if not ours or not theirs:
        return "unknown"
    return "match" if ours == theirs else "mismatch"


def screen(db: BankDB, name: str, dob: str, country: str, threshold: float) -> list[dict]:
    """Watchlist candidates for a party, as comparison facts. No names are returned."""
    wanted, hits = _norm(name), []
    for entry in db.watchlist():
        names = [entry["name"], *[a for a in entry["aliases"].split(";") if a]]
        best = max(SequenceMatcher(None, wanted, _norm(n)).ratio() for n in names)
        if best >= threshold:
            hits.append(
                {
                    "entry_id": entry["entry_id"],
                    "list": entry["list"],
                    "kind": entry["kind"],
                    "name_similarity": round(best, 2),
                    "dob_match": _compare(dob, entry["dob"]),
                    "country_match": _compare(country, entry["country"]),
                }
            )
    return sorted(hits, key=lambda h: (-h["name_similarity"], h["entry_id"]))


def hard_flags(db: BankDB, alert: Alert, policy: Policy) -> list[str]:
    """Facts that send an alert straight to the lead and bar a tier-1 close."""
    customer = db.customer(alert.customer_id)
    parties = [(customer["name"], customer["dob"], customer["country"])]
    for cid in alert.counterparty_ids:
        cp = db.counterparty(cid)
        parties.append((cp["name"], cp["dob"], cp["country"]))
    flags = []
    if any(screen(db, n, d, c, policy.screening_similarity) for n, d, c in parties):
        flags.append("WATCHLIST_CANDIDATE")
    if customer["pep"]:
        flags.append("PEP")
    if customer["risk_rating"] == "high":
        flags.append("HIGH_RISK_RATING")
    cutoff = alert.as_of - timedelta(days=policy.prior_escalation_days)
    priors = db.prior_alerts(alert.customer_id, before=alert.as_of)
    if any(
        p["outcome"] == "escalated" and date.fromisoformat(p["raised"]) >= cutoff for p in priors
    ):
        flags.append("PRIOR_ESCALATION")
    if alert.amount >= policy.large_amount:
        flags.append("LARGE_AMOUNT")
    return flags
