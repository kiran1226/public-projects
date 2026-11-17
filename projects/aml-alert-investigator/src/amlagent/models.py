"""Typed data shared by every module."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

REASON_CODES = (
    "documented_expected_activity",
    "consistent_with_profile",
    "recurring_known_counterparty",
    "explained_one_off",
    "screening_false_match",
)
TYPOLOGIES = (
    "structuring",
    "pass_through",
    "layering_related_parties",
    "profile_mismatch",
    "high_risk_jurisdiction",
    "sanctions_match",
    "other",
)
NOT_APPLICABLE = "not_applicable"  # strict tool schemas need every field; this marks "none"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Alert(Model):
    alert_id: str
    customer_id: str
    account_id: str
    rule: str
    raised: date
    as_of: date  # nothing after this date may be shown to a model
    window_start: date
    window_end: date
    amount: float
    txn_ids: list[str]
    counterparty_ids: list[str] = []
    split: Literal["dev", "heldout"] = "dev"


class Gold(Model):
    alert_id: str
    outcome: Literal["close", "escalate"]
    typology: str | None = None
    reason_code: str | None = None
    difficulty: Literal["clear", "needs_investigation"]
    key_evidence: list[str]  # real ids a good write-up must cite
    sensitive_relevant: bool = False
    scenario: str


class Finding(Model):
    statement: str
    evidence_ids: list[str]


class Disposition(Model):
    outcome: Literal["close_false_positive", "escalate_l2", "needs_info"]
    reason_code: str | None = None
    typology: str | None = None
    confidence: Literal["high", "medium", "low"]
    findings: list[Finding]
    narrative: str = ""
    open_questions: list[str] = []

    @model_validator(mode="after")
    def _consistent(self) -> Disposition:
        if self.outcome == "close_false_positive" and self.reason_code not in REASON_CODES:
            raise ValueError("a close needs a reason_code")
        if self.outcome == "escalate_l2":
            if self.typology not in TYPOLOGIES:
                raise ValueError("an escalation needs a typology")
            if not self.narrative.strip():
                raise ValueError("an escalation needs a narrative")
        return self


def _clean(data: dict) -> dict:
    return {k: (None if v == NOT_APPLICABLE else v) for k, v in data.items()}


def parse_submission(data: dict) -> Disposition:
    """Turn the input of `submit_disposition` into a Disposition. Raises ValidationError."""
    return Disposition.model_validate(_clean(data))


def parse_triage(data: dict) -> tuple[Disposition | None, str]:
    """Turn the input of `submit_triage` into (close disposition or None, handoff notes)."""
    if data.get("action") == "close":
        try:
            fields = _clean({k: data[k] for k in ("reason_code", "confidence", "findings")})
        except KeyError as exc:  # a vendor without strict schemas can omit a field
            raise ValueError(f"submit_triage is missing {exc}") from exc
        return Disposition(outcome="close_false_positive", **fields), ""
    if data.get("action") == "escalate":
        return None, str(data.get("handoff_notes", ""))
    raise ValueError("action must be close or escalate")


class ToolCall(Model):
    name: str
    input: dict
    output: str
    is_error: bool = False


class Step(Model):
    role: str  # triage | lead | worker:<type>
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    stop_reason: str = ""
    tool_calls: list[ToolCall] = []


class RuleHit(Model):
    code: str
    message: str


class CaseResult(Model):
    alert_id: str
    config: str
    tier: Literal["baseline", "triage", "lead"]
    disposition: Disposition | None
    queue: Literal["analyst_review", "senior_review", "manual_investigation", "auto_closed"]
    rules: list[RuleHit] = []
    hard_flags: list[str] = []
    steps: list[Step] = []
    sensitive_requests: list[dict] = []
    triage_notes: str = ""
    cost_usd: float = 0.0
    latency_s: float = 0.0
