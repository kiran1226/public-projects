"""Scoring in code; no LLM judge.

Expected routing is derived from the gold labels and the policy, not hand-labelled, so
labels do not go stale when a threshold changes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import mean

from .bankdb import BankDB
from .llm import CassetteProvider
from .models import CaseResult, Gold
from .privacy import Gateway, OutboundScanner, Vault

OUTCOMES = {"close_false_positive": "close", "escalate_l2": "escalate", "needs_info": "needs_info"}
SPLITS = {"dev": "Development set", "heldout": "Held-out set"}


@dataclass(frozen=True)
class Scored:
    alert_id: str
    split: str
    scenario: str
    gold_outcome: str
    outcome: str  # close | escalate | needs_info | none
    tier: str
    queue: str
    unsafe: bool
    under_escalated: bool
    over_escalated: bool
    correct: bool
    typology_ok: bool | None
    recall: float | None
    cited: int
    cited_valid: int
    sensitive_requests: int
    sensitive_justified: int
    cost_usd: float
    latency_s: float
    steps: int
    auto_closable: bool = False  # a clean tier-one close: what auto-close mode would close


def score_case(result: CaseResult, vault: Vault, gold: Gold, split: str) -> Scored:
    d = result.disposition
    trusted = d is not None and result.queue != "manual_investigation"
    outcome = OUTCOMES[d.outcome] if trusted else "none"
    unsafe = gold.outcome == "escalate" and outcome == "close"
    cited = [e for f in d.findings for e in f.evidence_ids] if d else []
    real = {vault.resolve(t) for t in cited}
    both_escalate = gold.outcome == "escalate" and outcome == "escalate"
    granted = sum(1 for entry in result.sensitive_requests if entry["granted"])
    return Scored(
        alert_id=result.alert_id,
        split=split,
        scenario=gold.scenario,
        gold_outcome=gold.outcome,
        outcome=outcome,
        tier=result.tier,
        queue=result.queue,
        unsafe=unsafe,
        under_escalated=unsafe and result.tier == "triage",
        over_escalated=gold.difficulty == "clear"
        and not result.hard_flags
        and result.tier == "lead",
        correct=outcome == gold.outcome,
        typology_ok=(d.typology == gold.typology) if both_escalate else None,
        recall=(
            len(real & set(gold.key_evidence)) / len(gold.key_evidence)
            if trusted and gold.key_evidence
            else None
        ),
        cited=len(cited),
        cited_valid=sum(t in vault.issued() for t in cited),
        sensitive_requests=granted,
        sensitive_justified=granted if gold.sensitive_relevant else 0,
        cost_usd=result.cost_usd,
        latency_s=result.latency_s,
        steps=len(result.steps),
        auto_closable=(
            result.tier == "triage"
            and outcome == "close"
            and result.queue in ("analyst_review", "auto_closed")
            and not result.rules
        ),
    )


def _mean(values: list) -> float | None:
    kept = [v for v in values if v is not None]
    return mean(kept) if kept else None


def summarise(rows: list[Scored]) -> dict:
    n = len(rows)
    cited = sum(r.cited for r in rows)
    return {
        "n": n,
        "unsafe": sum(r.unsafe for r in rows),
        "unsafe_ids": sorted(r.alert_id for r in rows if r.unsafe),
        "under_escalated": sum(r.under_escalated for r in rows),
        "over_escalated": sum(r.over_escalated for r in rows),
        "accuracy": sum(r.correct for r in rows) / n if n else None,
        "needs_info": sum(r.outcome == "needs_info" for r in rows),
        "no_recommendation": sum(r.outcome == "none" for r in rows),
        "typology_accuracy": _mean([r.typology_ok for r in rows]),
        "recall": _mean([r.recall for r in rows]),
        "citation_validity": sum(r.cited_valid for r in rows) / cited if cited else None,
        "cost_total": sum(r.cost_usd for r in rows),
        "cost_per_alert": sum(r.cost_usd for r in rows) / n if n else None,
        "latency": _mean([r.latency_s for r in rows]),
        "steps": _mean([r.steps for r in rows]),
        "tiers": dict(sorted(Counter(r.tier for r in rows).items())),
        "queues": dict(sorted(Counter(r.queue for r in rows).items())),
        "sensitive_requests": sum(r.sensitive_requests for r in rows),
        "sensitive_justified": sum(r.sensitive_justified for r in rows),
        "auto_closable": sum(r.auto_closable for r in rows),
        "auto_closable_unsafe": sum(r.auto_closable and r.unsafe for r in rows),
    }


def leak_scan(cassette: Path, identifiers: set[str]) -> list[tuple[str, list[str]]]:
    """Scan everything a recording sent for real identifiers. Empty means clean."""
    scanner = OutboundScanner(identifiers)
    hits = [(tag, scanner.scan(sent)) for tag, sent in CassetteProvider(cassette).sent()]
    return [(tag, kinds) for tag, kinds in hits if kinds]


def scrubber_catch_rate(db: BankDB, planted: list[str]) -> tuple[int, int]:
    """Of the free-text fields that hold an identifier, how many are clean after scrubbing."""
    scanner = OutboundScanner(db.identifier_inventory() | set(planted))
    gateway = Gateway(db, Vault("scrubber-probe"), date(2026, 12, 31))
    dirty = [t for t in db.texts() if scanner.scan(t)]
    still_dirty = [t for t in dirty if scanner.scan(gateway.scrub(t))]
    return len(dirty) - len(still_dirty), len(dirty)


@dataclass
class Section:
    name: str
    label: str
    rows: list[Scored] | None  # None: this configuration has no recording yet
    metered: bool = True
    note: str = ""


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.0f}%"


def _money(value: float | None, metered: bool) -> str:
    if not metered:
        return "trial tier"
    return "n/a" if value is None else f"${value:.4f}"


def _num(value: float | None, unit: str = "") -> str:
    return "n/a" if value is None else f"{value:.1f}{unit}"


def render_report(
    sections: list[Section], *, scrubber: tuple[int, int], leaks: dict[str, list]
) -> str:
    measured = [s for s in sections if s.rows is not None]
    out = [
        "# Evaluation report",
        "",
        "Generated by `aml-agent eval`. Scoring is done in code against gold labels; there is no "
        "LLM judge. The alert set is synthetic and deliberately enriched with hard cases, so the "
        "shares below are not what a production queue looks like.",
        "",
        "- **Suspicious closed**: gold says escalate, the system recommended a close. The unsafe "
        "error; the target is zero.",
        "- **Under-escalated**: an unsafe close made at the first tier.",
        "- **Over-escalated**: a clear, unflagged alert that reached the lead.",
        "- **No recommendation**: the case went to manual investigation (failure, limit, rejected "
        "citations, low confidence).",
        "",
    ]
    for split, title in SPLITS.items():
        out += [
            f"## {title}",
            "",
            "| Configuration | Alerts | Suspicious closed | Under-escalated | Over-escalated | "
            "Outcome accuracy | Typology | Evidence recall | Valid citations | No recommendation | "
            "Cost per alert | Total cost |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for section in measured:
            s = summarise([r for r in section.rows if r.split == split])
            out.append(
                f"| {section.label} | {s['n']} | {s['unsafe']} | {s['under_escalated']} | "
                f"{s['over_escalated']} | {_pct(s['accuracy'])} | {_pct(s['typology_accuracy'])} | "
                f"{_pct(s['recall'])} | {_pct(s['citation_validity'])} | "
                f"{s['no_recommendation']} | "
                f"{_money(s['cost_per_alert'], section.metered)} | "
                f"{_money(s['cost_total'], section.metered)} |"
            )
        out.append("")

    out += [
        "## Routing and review queues (all alerts)",
        "",
        "| Configuration | Tier 1 | Lead | Analyst review | Senior review | Manual | "
        "Would auto-close | Avg steps | Avg latency |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for section in measured:
        s = summarise(section.rows)
        tiers, queues = s["tiers"], s["queues"]
        out.append(
            f"| {section.label} | {tiers.get('triage', 0) + tiers.get('baseline', 0)} | "
            f"{tiers.get('lead', 0)} | {queues.get('analyst_review', 0)} | "
            f"{queues.get('senior_review', 0)} | {queues.get('manual_investigation', 0)} | "
            f"{s['auto_closable']} | {_num(s['steps'])} | {_num(s['latency'], ' s')} |"
        )
    out.append("")
    for section in measured:
        s = summarise(section.rows)
        if s["auto_closable"]:
            out += [
                f"{section.label}: in auto-close mode {s['auto_closable']} alerts would have "
                f"been closed without review, {s['auto_closable_unsafe']} of them suspicious.",
                "",
            ]

    routed = next((s for s in measured if s.name == "routed"), None)
    if routed:
        lead_costs = [r.cost_usd for r in routed.rows if r.tier == "lead"]
        total = sum(r.cost_usd for r in routed.rows)
        single = next((s for s in measured if s.name == "opus"), None)
        out += ["## Routing against one large model", ""]
        if lead_costs:
            out += [
                f"Measured: the routed configuration cost ${total:.2f} for {len(routed.rows)} "
                f"alerts; a case handled by the lead cost ${mean(lead_costs):.3f} on average.",
                "",
            ]
        if single:
            right = sum(r.correct for r in single.rows)
            routed_right = sum(r.correct for r in routed.rows)
            out += [
                f"Measured: one large model on every alert, with no first tier, cost "
                f"${sum(r.cost_usd for r in single.rows):.2f} for {len(single.rows)} alerts and "
                f"got {right} of {len(single.rows)} outcomes right, against {routed_right} of "
                f"{len(routed.rows)} for the routed configuration. It runs the lead at low "
                "effort; the routed configuration runs it at medium. With no first tier, "
                "every clear alert counts as over-escalated in the tables above.",
                "",
            ]
        elif lead_costs:
            estimate = mean(lead_costs) * len(routed.rows)
            out += [
                f"Estimate, not measured: sending every alert to the lead would cost about "
                f"${estimate:.2f} for the same set (average lead-case cost times the number of "
                "alerts).",
                "",
            ]

    caught, total_texts = scrubber
    out += [
        "## Data protection",
        "",
        f"- Scrubber: {caught} of {total_texts} free-text fields that hold an identifier are "
        "clean after scrubbing (measured on the whole dataset, including identifiers planted "
        "in free text that are in no entity table).",
        "- Leak scan of everything sent to a model, per recording:",
    ]
    for name, hits in sorted(leaks.items()):
        found = "none" if not hits else f"{len(hits)} request{'s' if len(hits) > 1 else ''}"
        out.append(f"  - {name}: {found}")
        out += [f"    - {tag}: {', '.join(kinds)}" for tag, kinds in hits]
    for section in measured:
        s = summarise(section.rows)
        if s["sensitive_requests"]:
            out.append(
                f"- {section.label}: {s['sensitive_requests']} sensitive-category requests, "
                f"{s['sensitive_justified']} on alerts where the labels mark it relevant."
            )
    out.append("")

    missing = [s for s in sections if s.rows is None]
    out += ["## Not measured", ""]
    out += [f"- {s.label}: {s.note or 'not recorded'}" for s in missing]
    if not any(s.name == "opus" for s in measured):
        out += [
            "- A single large model at low effort on every alert is the simpler alternative "
            "to routing. It has not been measured.",
        ]
    out.append("")
    return "\n".join(out)
