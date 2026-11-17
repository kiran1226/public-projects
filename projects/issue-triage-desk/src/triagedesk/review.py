"""A reviewer's decisions on the desk's proposals, kept on this machine.

A person reads a proposal in the triage queue and accepts it, corrects it or rejects
it. Each decision is one JSON line, appended and never rewritten. A later decision on
the same proposal supersedes the earlier one when the log is read, but the earlier
line stays in the file, so a change of mind is on record.

The log holds issue numbers, label names and the reviewer's own comment. It holds no
issue text. The default file is `data/corrections/reviews.jsonl`. The repository
ignores `data/corrections/`, so decisions made while trying the demo are not
published; commit them in a real deployment.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from .config import PROJECT_ROOT, Settings
from .models import Prediction, Proposal

DEFAULT_PATH = PROJECT_ROOT / "data" / "corrections" / "reviews.jsonl"
VERDICTS = ("accepted", "corrected", "rejected")
COMMENT_MAX = 500

Verdict = Literal["accepted", "corrected", "rejected"]
# A proposal is one issue under one system version. The version already says which
# configuration answered, so the pair names one row of the prediction log.
Key = tuple[int, str]


def path_for(settings: Settings) -> Path:
    """The review file that belongs to these settings: next to the logs.

    With the defaults this is DEFAULT_PATH. When TRIAGEDESK_HOME moves the logs, the
    reviews move with them.
    """
    return settings.log_dir.parent / "corrections" / "reviews.jsonl"


class Review(BaseModel):
    """One decision on one proposal."""

    number: int
    system_version: str
    config: str
    verdict: Verdict
    # The labels the reviewer says are right. For an accepted proposal these are the
    # labels it proposed at medium or high confidence. A rejection records no answer.
    labels: list[str] = Field(default_factory=list)
    # The reviewer's answer to the duplicate question. None: not a duplicate.
    duplicate_of: int | None = Field(default=None, gt=0)
    # Always with a time zone: a decision without one cannot be ordered against others.
    decided_at: AwareDatetime
    comment: str | None = Field(default=None, max_length=COMMENT_MAX)

    @field_validator("labels")
    @classmethod
    def _sorted_once(cls, labels: list[str]) -> list[str]:
        return sorted(set(labels))

    @field_validator("comment", mode="before")
    @classmethod
    def _no_empty_comment(cls, comment):
        if isinstance(comment, str):
            comment = comment.strip()
        return comment or None

    @model_validator(mode="after")
    def _not_its_own_duplicate(self) -> Review:
        if self.duplicate_of == self.number:
            raise ValueError("An issue cannot be a duplicate of itself.")
        return self

    @property
    def key(self) -> Key:
        return (self.number, self.system_version)


def _review(prediction: Prediction, verdict: str, labels, duplicate_of, comment, now) -> Review:
    return Review(
        number=prediction.number,
        system_version=prediction.system_version,
        config=prediction.config,
        verdict=verdict,
        labels=list(labels),
        duplicate_of=duplicate_of,
        decided_at=now or datetime.now(UTC),
        comment=comment,
    )


def accept(
    prediction: Prediction, *, comment: str | None = None, now: datetime | None = None
) -> Review:
    """The proposal is right as it stands: its labels and its duplicate answer."""
    proposal = prediction.proposal
    return _review(prediction, "accepted", proposal.names(), proposal.duplicate_of, comment, now)


def correct(
    prediction: Prediction,
    labels: Iterable[str],
    duplicate_of: int | None,
    *,
    comment: str | None = None,
    now: datetime | None = None,
) -> Review:
    """The reviewer's own answer. If it equals the proposal, that is an acceptance."""
    made = _review(prediction, "corrected", labels, duplicate_of, comment, now)
    proposal = prediction.proposal
    if made.labels == proposal.names() and made.duplicate_of == proposal.duplicate_of:
        return made.model_copy(update={"verdict": "accepted"})
    return made


def reject(
    prediction: Prediction, *, comment: str | None = None, now: datetime | None = None
) -> Review:
    """The proposal is wrong and the reviewer does not say what is right."""
    return _review(prediction, "rejected", [], None, comment, now)


class ReviewLog:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)

    def add(self, review: Review) -> Review:
        """Append one decision. Nothing already in the file is touched."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(review.model_dump(mode="json"), ensure_ascii=False) + "\n")
        return review

    def read(self) -> list[Review]:
        """Every decision ever recorded, in the order it was written."""
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as handle:
            return [Review.model_validate(json.loads(line)) for line in handle if line.strip()]

    def latest(self) -> dict[Key, Review]:
        """The decision in force for each proposal, by (number, system_version).

        The newest `decided_at` wins. Of two made at the same moment, the one written
        later wins.
        """
        newest: dict[Key, Review] = {}
        for review in self.read():
            held = newest.get(review.key)
            if held is None or review.decided_at >= held.decided_at:
                newest[review.key] = review
        return newest

    def for_issue(self, number: int) -> list[Review]:
        """Every decision on one issue, under any system version, in the order written."""
        return [review for review in self.read() if review.number == number]


@dataclass(frozen=True)
class Summary:
    """Counts over a set of decisions."""

    accepted: int = 0
    corrected: int = 0
    rejected: int = 0
    # The three below are counted over corrected decisions whose proposal was passed.
    compared: int = 0
    labels_added: int = 0
    labels_removed: int = 0
    duplicates_changed: int = 0

    @property
    def decided(self) -> int:
        return self.accepted + self.corrected + self.rejected


def summary(reviews: Iterable[Review], proposals: Mapping[Key, Proposal] | None = None) -> Summary:
    """Count decisions per verdict and, for corrections, what the reviewer changed.

    Pass `log.latest().values()` to count the decisions in force. `proposals` maps
    (number, system_version) to the proposal that was reviewed. A correction whose
    proposal is not in it is counted as corrected and left out of the label counts.
    """
    counts = dict.fromkeys(VERDICTS, 0)
    compared = added = removed = duplicates = 0
    for review in reviews:
        counts[review.verdict] += 1
        proposal = (proposals or {}).get(review.key)
        if review.verdict != "corrected" or proposal is None:
            continue
        compared += 1
        proposed, right = set(proposal.names()), set(review.labels)
        added += len(right - proposed)
        removed += len(proposed - right)
        duplicates += review.duplicate_of != proposal.duplicate_of
    return Summary(
        accepted=counts["accepted"],
        corrected=counts["corrected"],
        rejected=counts["rejected"],
        compared=compared,
        labels_added=added,
        labels_removed=removed,
        duplicates_changed=duplicates,
    )
