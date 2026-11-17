"""Rules-only configuration: answers "does this need a model at all?" with numbers.

It runs through the same gateway, evidence ledger and policy gate as the agent, and it
needs no API key.
"""

from __future__ import annotations

from . import features
from .bankdb import BankDB
from .models import Alert, CaseResult, Disposition, Finding
from .policy import Policy, gate
from .privacy import Gateway, PartyIndex, Vault


def _typology(
    db: BankDB, alert: Alert, flags: list[str], summary: dict, policy: Policy
) -> str | None:
    if "WATCHLIST_CANDIDATE" in flags:
        return "sanctions_match"
    if summary["structuring"]["count"] >= 3:
        return "structuring"
    if summary["pass_through_ratio"] >= 0.8 and summary["distinct_senders"] >= 4:
        return "pass_through"
    countries = {db.counterparty(c)["country"] for c in alert.counterparty_ids}
    if countries & policy.high_risk_countries:
        return "high_risk_jurisdiction"
    if summary["turnover_ratio"] is not None and summary["turnover_ratio"] >= 3:
        return "profile_mismatch"
    return "other" if flags else None


def investigate_baseline(
    alert: Alert,
    *,
    db: BankDB,
    policy: Policy | None = None,
    mode: str = "assisted",
    index: PartyIndex | None = None,
) -> tuple[CaseResult, Vault]:
    policy = policy or Policy()
    vault = Vault(alert.alert_id)
    gateway = Gateway(db, vault, alert.as_of, index)
    flags = features.hard_flags(db, alert, policy)
    owner = db.customer(alert.customer_id)
    summary = features.activity_summary(
        db,
        alert.account_id,
        alert.window_start,
        alert.window_end,
        owner["expected_monthly_turnover"],
        policy,
    )
    evidence = [gateway.tok("TXN", t) for t in alert.txn_ids[:10]]
    customer = gateway.tok("CUST", alert.customer_id)
    typology = _typology(db, alert, flags, summary, policy)
    if typology:
        disposition = Disposition(
            outcome="escalate_l2",
            typology=typology,
            confidence="medium",
            findings=[
                Finding(
                    statement=f"Rule indicators for {typology} on the alert window.",
                    evidence_ids=evidence,
                )
            ],
            narrative=(
                f"{customer}: escalated by rules for {typology}. No review of the customer file."
            ),
        )
    else:
        disposition = Disposition(
            outcome="close_false_positive",
            reason_code="consistent_with_profile",
            confidence="high",
            findings=[
                Finding(
                    statement="No rule indicator fired on the alert window.", evidence_ids=evidence
                )
            ],
        )
    queue, rules = gate(
        disposition,
        tier="baseline",
        hard_flags=flags,
        shown=vault.issued(),  # no model: the rules cite what code retrieved
        failure=None,
        sensitive_used=False,
        mode=mode,
        alert_id=alert.alert_id,
        policy=policy,
    )
    result = CaseResult(
        alert_id=alert.alert_id,
        config="baseline",
        tier="baseline",
        disposition=disposition,
        queue=queue,
        rules=rules,
        hard_flags=flags,
    )
    return result, vault
