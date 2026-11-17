"""The records every step passes on. Fields the feed may omit are optional, and stay None."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class Lot(BaseModel):
    title: str | None = None
    description: str | None = None


class Notice(BaseModel):
    """A competition notice: a buyer asking for tenders."""

    publication_number: str
    publication_date: date
    notice_type: str
    procedure_id: str
    buyer_country: str | None = None
    buyer_name: str | None = None
    language: str | None = None
    title: str = ""
    description: str = ""
    lots: list[Lot] = Field(default_factory=list)
    cpv: list[str] = Field(default_factory=list)
    deadline: date | None = None
    estimated_value: float | None = None
    currency: str | None = None
    procedure_type: str | None = None
    nature: str | None = None
    conditions: list[str] = Field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://ted.europa.eu/en/notice/-/detail/{self.publication_number}"

    @property
    def text(self) -> str:
        """Everything a ranker reads: title, description and the lots."""
        parts = [self.title, self.description]
        for lot in self.lots:
            if lot.title and lot.title != self.title:
                parts.append(lot.title)
            if lot.description and lot.description != self.description:
                parts.append(lot.description)
        return "\n".join(p for p in parts if p)


class Winner(BaseModel):
    name: str | None = None
    identifier: str | None = None
    country: str | None = None
    size: str | None = None


class Award(BaseModel):
    """A contract award notice: the outcome of a procedure."""

    publication_number: str
    publication_date: date
    procedure_id: str
    buyer_country: str | None = None
    buyer_name: str | None = None
    title: str = ""
    description: str = ""
    cpv: list[str] = Field(default_factory=list)
    winners: list[Winner] = Field(default_factory=list)
    value: float | None = None
    currency: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(p for p in (self.title, self.description) if p)


class PastWin(BaseModel):
    """One earlier award a profile is built from."""

    procedure_id: str
    publication_date: date
    buyer_country: str | None = None
    # A hash of the buying organisation, see awards.buyer_key.
    buyer: str | None = None
    cpv: list[str] = Field(default_factory=list)
    text: str = ""


class Profile(BaseModel):
    """What a supplier sells and where. Built from past wins, or written by hand."""

    profile_id: str
    kind: Literal["supplier", "client"] = "supplier"
    name: str = ""
    description: str = ""
    countries: list[str] = Field(default_factory=list)
    cpv_weights: dict[str, float] = Field(default_factory=dict)
    texts: list[str] = Field(default_factory=list)
    # Buying organisations this supplier has won from, hashed, with the number of wins.
    buyers: dict[str, int] = Field(default_factory=dict)
    past_wins: int = 0
    min_value_eur: float | None = None
    max_value_eur: float | None = None
    min_days_to_deadline: int = 0
    excluded_procedure_types: list[str] = Field(default_factory=list)
    excluded_natures: list[str] = Field(default_factory=list)
    # What the client can show a buyer, for the model to compare conditions against.
    capabilities: list[str] = Field(default_factory=list)


class RuleHit(BaseModel):
    rule: str
    detail: str


class Scored(BaseModel):
    procedure_id: str
    publication_number: str
    score: float


Decision = Literal["bid", "skip", "ask_person"]


class Condition(BaseModel):
    """Something the notice requires of a tenderer, with the passage that says so."""

    requirement: str = Field(description="The condition, in one short English sentence.")
    quote: str = Field(
        description="The passage of the notice that states it, copied character for character."
    )
    met: Literal["yes", "no", "unknown"] = Field(
        description="Whether the supplier profile shows this condition is met."
    )


class Assessment(BaseModel):
    """What the model returns for one notice and one profile."""

    # The range is enforced by the gate, not the schema: structured outputs accept only
    # a subset of JSON Schema, and a number bound is not worth a rejected request.
    fit: int = Field(description="0 to 100: how well the tender fits what the supplier sells.")
    decision: Decision
    reason: str = Field(description="One or two English sentences a bid manager can act on.")
    reason_quote: str = Field(
        description="The passage of the notice the reason rests on, copied character for character."
    )
    conditions: list[Condition] = Field(default_factory=list)


class GatedAssessment(BaseModel):
    """An assessment after the gate: only what the notice itself supports."""

    fit: int
    decision: Decision
    reason: str
    reason_quote: str | None = None
    conditions: list[Condition] = Field(default_factory=list)
    dropped_conditions: int = 0
    downgraded: bool = False
    cost_usd: float = 0.0


class ShortlistEntry(BaseModel):
    rank: int
    publication_number: str
    score: float
    assessment: GatedAssessment | None = None


class Shortlist(BaseModel):
    """One prediction: the list written for one profile on one publication day."""

    profile_id: str
    day: date
    ranker: str
    system_version: str
    mode: Literal["backtest", "live"]
    written_at: datetime
    candidates: int = 0
    removed: dict[str, int] = Field(default_factory=dict)
    entries: list[ShortlistEntry] = Field(default_factory=list)
    skipped_assessments: int = 0
    cost_usd: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.profile_id}|{self.day.isoformat()}|{self.ranker}|{self.system_version}"


class Outcome(BaseModel):
    """The real answer: a supplier won a procedure, and when the feed said so.

    `publication_number` and `day` point at the tender as first published, which is
    what a list written that day could have contained. `filter_hits` records which
    hard rules the tender broke for this profile; it is a fact about the tender and
    the profile, not about any prediction.
    """

    profile_id: str
    procedure_id: str
    arrived: date
    publication_number: str | None = None
    day: date | None = None
    filter_hits: list[str] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.profile_id}|{self.procedure_id}"
