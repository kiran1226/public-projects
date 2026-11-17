"""Business policy: thresholds here, named rules and the gate below (Task 10)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .models import Disposition, RuleHit


@dataclass(frozen=True)
class Policy:
    reporting_threshold: float = 10_000.0  # cash reporting threshold
    structuring_band: float = 0.8  # deposits from 80% of the threshold count as "just under"
    large_amount: float = 100_000.0
    screening_similarity: float = 0.85
    prior_escalation_days: int = 365
    high_risk_countries: frozenset[str] = frozenset({"VD", "KV"})  # fictional
    triage_close_reasons: frozenset[str] = frozenset(
        {
            "documented_expected_activity",
            "consistent_with_profile",
            "recurring_known_counterparty",
            "explained_one_off",
        }
    )
    max_rows: int = 40  # raw transactions shown to a tool-using model per call
    max_worker_rows: int = 200
    max_delegations: int = 6
    case_cost_limit_usd: float = 0.60
    qa_sample_rate: float = 0.10


RULES = {
    "CITATION_UNVERIFIED": "A finding cites evidence the agent never retrieved in this case.",
    "FINDING_UNCITED": "A finding has no evidence, or the disposition has no findings.",
    "LOW_CONFIDENCE": "The agent marked its own conclusion as a guess.",
    "CLOSE_WITH_HARD_FLAG": "A close is proposed on an alert that carries a hard risk flag.",
    "LIMIT_EXCEEDED": "The investigation hit its step, delegation or cost limit.",
    "MODEL_FAILURE": "The model call failed, was refused, or returned unusable output.",
    "LEAK_BLOCKED": "The outbound check stopped a request that contained a real identifier.",
    "SENSITIVE_ACCESS_USED": "The agent requested a withheld sensitive category in this case.",
    "TRIAGE_CONFIDENCE": "Only a high-confidence close may stand at the first tier.",
    "TRIAGE_REASON_NOT_ALLOWED": "This reason code may not close an alert at the first tier.",
    "QA_SAMPLE": "Sampled from automatic closes for a quality check.",
    "INSTRUCTION_IN_DATA": "Free text in the bank's data reads like an instruction to the model; "
    "nothing the agent concluded is relied on.",
    "INTERNAL_ERROR": "The agent hit an internal error on this case; nothing it produced is used.",
}


def _hit(code: str) -> RuleHit:
    return RuleHit(code=code, message=RULES[code])


def citation_hits(disposition: Disposition, shown: set[str]) -> list[RuleHit]:
    """The evidence ledger check: every cited token must have been shown, in this case, to
    the model that cites it. It proves the evidence was retrieved, not that it supports
    the statement; that judgement stays with the analyst."""
    hits = []
    needs_findings = disposition.outcome != "needs_info"
    if (needs_findings and not disposition.findings) or any(
        not f.evidence_ids for f in disposition.findings
    ):
        hits.append(_hit("FINDING_UNCITED"))
    if any(e not in shown for f in disposition.findings for e in f.evidence_ids):
        hits.append(_hit("CITATION_UNVERIFIED"))
    return hits


def triage_close_hits(disposition: Disposition, shown: set[str], policy: Policy) -> list[RuleHit]:
    """Reasons a first-tier close may not stand. Empty means it may."""
    hits = citation_hits(disposition, shown)
    if disposition.confidence != "high":
        hits.append(_hit("TRIAGE_CONFIDENCE"))
    if disposition.reason_code not in policy.triage_close_reasons:
        hits.append(_hit("TRIAGE_REASON_NOT_ALLOWED"))
    return hits


def _sampled(alert_id: str, rate: float) -> bool:
    bucket = int(hashlib.sha256(alert_id.encode()).hexdigest(), 16) % 10_000
    return bucket < rate * 10_000


def gate(
    disposition: Disposition | None,
    *,
    tier: str,
    hard_flags: list[str],
    shown: set[str],
    failure: str | None,
    sensitive_used: bool,
    mode: str,
    alert_id: str,
    policy: Policy,
    instruction_in_data: bool = False,
) -> tuple[str, list[RuleHit]]:
    """Decide the queue. No failure and no rejected output can produce a close."""
    if instruction_in_data:
        # Something the model read was written to it. Its conclusion may have been steered,
        # so a person takes the case whatever the model proposed and however sure it was.
        return "manual_investigation", [_hit("INSTRUCTION_IN_DATA")]
    if failure or disposition is None:
        code = {"limit": "LIMIT_EXCEEDED", "leak_blocked": "LEAK_BLOCKED"}.get(
            failure or "", "MODEL_FAILURE"
        )
        return "manual_investigation", [_hit(code)]

    blocking = citation_hits(disposition, shown)
    if disposition.confidence == "low":
        blocking.append(_hit("LOW_CONFIDENCE"))
    if blocking:
        return "manual_investigation", blocking

    notes = [_hit("SENSITIVE_ACCESS_USED")] if sensitive_used else []
    closing = disposition.outcome == "close_false_positive"
    if closing and hard_flags:
        return "senior_review", [_hit("CLOSE_WITH_HARD_FLAG"), *notes]
    if closing and tier == "triage" and mode == "auto-close":
        if _sampled(alert_id, policy.qa_sample_rate):
            return "analyst_review", [_hit("QA_SAMPLE"), *notes]
        return "auto_closed", notes
    return "analyst_review", notes
