"""The records that move through the cascade.

No record has a field for who made an edit. The parsers read the account name only to
tell a temporary account from a registered one, and keep that one bit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "vandalism",
    "spam_or_promotion",
    "false_or_unsourced_claim",
    "test_or_accident",
    "content_removal",
    "other",
    "none",
]


class Edit(BaseModel):
    """One edit to an article, as the stream or the recent-changes list reports it."""

    rev_id: int
    parent_id: int
    title: str
    timestamp: datetime
    minor: bool = False
    bot: bool = False
    # True for a temporary account, which is what an editor without an account gets.
    temp: bool = False
    old_len: int = 0
    new_len: int = 0
    # The edit summary, with links to user pages and names in revert summaries removed.
    comment: str = ""
    # When the stream delivered the event. None for history.
    event_time: datetime | None = None

    @property
    def delta(self) -> int:
        return self.new_len - self.old_len


class Change(BaseModel):
    """What an edit did to the page text, reduced to the lines that differ."""

    rev_id: int
    # Numbered lines: "3 - old text" for removed, "3 + new text" for added.
    lines: list[str] = Field(default_factory=list)
    truncated: bool = False
    # False when a revision's text could not be read (deleted or hidden).
    available: bool = True

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class Tier2Answer(BaseModel):
    """What the model returns for one edit. No free text."""

    risk: int = Field(description="0 to 100: how likely the edit is one editors would revert.")
    category: Category = Field(description="The kind of problem, or none.")
    first_line: int = Field(description="First line number of the risky part, or 0 if none.")
    last_line: int = Field(description="Last line number of the risky part, or 0 if none.")


class Verdict(BaseModel):
    """The cascade's answer for one edit, after the gate."""

    # The probability of a revert that orders the queue.
    risk: float
    tier1: float
    # The model's 0 to 100 answer, where a model read the edit and the gate kept it.
    model_risk: int | None = None
    category: str | None = None
    first_line: int | None = None
    last_line: int | None = None
    # Which tier decided: "tier1" or "model".
    source: str = "tier1"
    # Why the model did not decide, where the edit was in its slice.
    skipped: str | None = None
    # Gate rules that fired.
    gate: list[str] = Field(default_factory=list)


class Prediction(BaseModel):
    """One row of the prediction log."""

    rev_id: int
    made_at: datetime
    written_at: datetime
    system_version: str
    config: str
    verdict: Verdict
    cost_usd: float = 0.0
    # Seconds from the edit's event to its place in the queue. None in a backtest.
    latency_s: float | None = None

    @property
    def key(self) -> tuple[int, str, str]:
        return (self.rev_id, self.config, self.system_version)


class Outcome(BaseModel):
    """What became of an edit, read after the waiting period."""

    rev_id: int
    made_at: datetime
    read_at: datetime
    reverted: bool
    # False if the revision could no longer be read when the outcome was collected.
    found: bool = True
