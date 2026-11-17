"""The records that move through the desk.

No record has a field for a person: no reporter, no assignee, no commenter. The feed
parser never reads those fields, so they cannot reach the store or a log.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]
CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


class Issue(BaseModel):
    """One issue as the tracker returned it. Title and body are kept as first seen."""

    number: int
    title: str
    body: str = ""
    created_at: datetime
    updated_at: datetime | None = None
    closed_at: datetime | None = None
    state: str = "open"
    state_reason: str | None = None
    labels: list[str] = Field(default_factory=list)

    @property
    def closed_as_duplicate(self) -> bool:
        return self.state == "closed" and self.state_reason == "duplicate"


class LabelInfo(BaseModel):
    """A label as the repository defines it."""

    name: str
    description: str = ""


class TimelineEvent(BaseModel):
    """One entry of an issue's timeline, reduced to what truth needs.

    A comment is kept as the issue numbers it refers to and whether it speaks of a
    duplicate. Its text and its author are not kept.
    """

    event: str
    created_at: datetime | None = None
    # "User" or "Bot": the kind of account, never its name.
    actor_type: str | None = None
    label: str | None = None
    state_reason: str | None = None
    refs: list[int] = Field(default_factory=list)
    # References to issues in other repositories, as "owner/name#number".
    outside_refs: list[str] = Field(default_factory=list)
    says_duplicate: bool = False
    # For a "renamed" event: the title before the rename.
    old_title: str | None = None


class LabelDefinition(BaseModel):
    name: str
    group: str
    description: str = ""
    # Words that mark this label in the training history, most telling first.
    terms: list[str] = Field(default_factory=list)
    # Titles of two training issues that carry only this label of its group.
    examples: list[str] = Field(default_factory=list)
    training_issues: int = 0


class Taxonomy(BaseModel):
    """The labels the desk may propose, with a definition for each."""

    repo: str
    built_from_issues: int = 0
    labels: list[LabelDefinition] = Field(default_factory=list)
    # Labels of a configured group that were too rare to keep.
    dropped: dict[str, int] = Field(default_factory=dict)

    def names(self, group: str | None = None) -> list[str]:
        return [d.name for d in self.labels if group is None or d.group == group]

    def group_of(self, name: str) -> str | None:
        for definition in self.labels:
            if definition.name == name:
                return definition.group
        return None

    def keep(self, labels: list[str]) -> list[str]:
        """The labels of an issue that are in the taxonomy, sorted."""
        allowed = set(self.names())
        return sorted(label for label in labels if label in allowed)


class Candidate(BaseModel):
    """An earlier issue that may be the same report."""

    number: int
    score: float
    # Place in the word search and in the vector search, where it was found.
    lexical_rank: int | None = None
    vector_rank: int | None = None
    similarity: float | None = None


class LabelPick(BaseModel):
    name: str
    confidence: Confidence


class Triage(BaseModel):
    """What the model returns for one issue."""

    labels: list[LabelPick] = Field(
        description="Labels from the list that apply. Empty if none clearly applies."
    )
    duplicate_of: int | None = Field(
        description="Number of the earlier issue that reports the same problem, or null."
    )
    duplicate_confidence: Confidence = Field(
        description="How sure the duplicate answer is, including when it is null."
    )
    note: str = Field(description="One sentence for the triager, at most 30 words.")


class Proposal(BaseModel):
    """The desk's answer for one issue, after the gate."""

    labels: list[LabelPick] = Field(default_factory=list)
    duplicate_of: int | None = None
    duplicate_confidence: Confidence | None = None
    # Why a person has to look. Empty means the desk would act on its own.
    person: list[str] = Field(default_factory=list)
    # What the gate changed, as rule names.
    gate: list[str] = Field(default_factory=list)
    # Which step produced the answer: rules, classifier or model.
    source: str = "rules"

    @property
    def needs_person(self) -> bool:
        return bool(self.person)

    def names(self, at_least: Confidence = "medium") -> list[str]:
        floor = CONFIDENCE_ORDER[at_least]
        return sorted(p.name for p in self.labels if CONFIDENCE_ORDER[p.confidence] >= floor)


class Prediction(BaseModel):
    """One row of the prediction log."""

    number: int
    created_at: datetime
    written_at: datetime
    system_version: str
    config: str
    proposal: Proposal
    # Earlier issues retrieved, best first.
    candidates: list[int] = Field(default_factory=list)
    cost_usd: float = 0.0
    latency_ms: int = 0
    # What the tracker already showed when the prediction was written. A live
    # prediction is scored on labels only if no label of the taxonomy was there yet.
    seen_labels: bool = False
    seen_closed: bool = False

    @property
    def key(self) -> tuple[int, str, str]:
        return (self.number, self.config, self.system_version)


class Outcome(BaseModel):
    """What the maintainers did with one issue, read after the waiting period."""

    number: int
    created_at: datetime
    read_at: datetime
    # Labels of the taxonomy on the issue when it was read.
    labels: list[str] = Field(default_factory=list)
    state_reason: str | None = None
    duplicate: bool = False
    # The issue it was closed as a duplicate of, where one could be resolved.
    original: int | None = None
    # resolved, unresolved (no reference found), outside (another repository) or
    # not_fetched (the timeline was not read yet).
    original_status: str | None = None
