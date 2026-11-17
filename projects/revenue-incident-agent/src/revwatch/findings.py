"""What an investigation produces, and the closed lists it has to choose from."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from revwatch.scenarios import Action, Cause, Mitigation

Confidence = Literal["high", "medium", "low"]
SUMMARY_WORDS = 120

# The one reversible action each cause may carry. Policy, not a suggestion: a finding
# that proposes anything else is held.
ALLOWED_MITIGATION: dict[str, Action | None] = {
    "payment_provider_failure": "failover_payment_provider",
    "release_regression": "rollback_release",
    "coupon_misconfiguration": "disable_coupon",
    "price_error": "pause_listing",
    "bot_traffic": "enable_bot_challenge",
    "tracking_outage": None,
    "campaign_change": "resume_campaign",
    "expected_event": None,
    "unknown": None,
}

# Which change-log entries can stand behind a cause.
CAUSE_OF_CHANGE: dict[str, Cause] = {
    "provider_status": "payment_provider_failure",
    "release": "release_regression",
    "coupon": "coupon_misconfiguration",
    "price": "price_error",
    "campaign": "campaign_change",
    "tracking_tag": "tracking_outage",
}

# The target key that carries the entity of the blamed change, per action.
ENTITY_KEY: dict[str, str] = {
    "failover_payment_provider": "provider",
    "rollback_release": "release_id",
    "disable_coupon": "code",
    "pause_listing": "sku",
    "resume_campaign": "campaign_id",
}


class Citation(BaseModel):
    """One number in the brief: where it came from, so that code can recompute it."""

    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(description="Id of the tool call the value came from, e.g. c3")
    row: str = Field(description="The row's key in that result")
    column: str = Field(description="The column the value is in")
    value: float = Field(description="The value as returned")


class Finding(BaseModel):
    """The outcome of one investigation. `unknown` with no mitigation is a valid finding."""

    model_config = ConfigDict(extra="forbid")

    cause: Cause
    change_id: str | None = Field(
        default=None, description="The change-log entry blamed, or null if none is"
    )
    affected_slice: dict[str, str] = Field(
        default_factory=dict, description="Dimension values the problem is limited to"
    )
    evidence: list[Citation] = Field(default_factory=list)
    confidence: Confidence
    mitigation: Mitigation | None = None
    summary: str = Field(description=f"Brief for the on-call, at most {SUMMARY_WORDS} words")

    @field_validator("summary")
    @classmethod
    def _short(cls, text: str) -> str:
        words = len(text.split())
        if words > SUMMARY_WORDS:
            raise ValueError(f"summary has {words} words; the limit is {SUMMARY_WORDS}")
        return text
