"""A reviewer's decisions on queue entries, kept on this machine.

A person reads an edit in the review queue and says what it is: a problem, fine, or
that they cannot tell. Each decision is one JSON line, appended and never rewritten.
A later decision on the same entry supersedes the earlier one when the log is read,
but the earlier line stays in the file, so a change of mind is on record.

The log holds revision ids, a verdict, a category from the fixed list and the
reviewer's own comment. It holds no page text, no edit summary and no account name,
and it has no field for who decided. The default file is
`data/corrections/reviews.jsonl`. The repository ignores `data/corrections/`, so
decisions made while trying the demo are not published; commit them in a real
deployment.

A decision is a note on this machine. Nothing here, and nothing that reads this log,
writes to a wiki.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from .config import CATEGORIES, PROJECT_ROOT, Settings
from .evals import rank
from .models import Prediction

DEFAULT_PATH = PROJECT_ROOT / "data" / "corrections" / "reviews.jsonl"
VERDICTS = ("problem", "fine", "unsure")
COMMENT_MAX = 500
# The part of a queue a reviewer is taken to reach, as in the evaluation's top 10%.
TOP_SHARE = 0.10
# What "no problem" is called in the fixed list. A reviewer says that with "fine".
NO_PROBLEM = "none"

Verdict = Literal["problem", "fine", "unsure"]
# A queue entry is one edit under one system version. The version already says which
# configuration answered, so the pair names one row of the prediction log.
Key = tuple[int, str]


def path_for(settings: Settings) -> Path:
    """The review file that belongs to these settings: next to the logs.

    With the defaults this is DEFAULT_PATH. When EDITWATCH_HOME moves the logs, the
    reviews move with them.
    """
    return settings.log_dir.parent / "corrections" / "reviews.jsonl"


class Review(BaseModel):
    """One decision on one queue entry."""

    rev_id: int
    system_version: str
    verdict: Verdict
    # What the reviewer says the problem is, from the list the model answers from.
    # None: the reviewer did not say, or did not call the edit a problem.
    category: str | None = None
    # Always with a time zone: a decision without one cannot be ordered against others.
    decided_at: AwareDatetime
    comment: str | None = Field(default=None, max_length=COMMENT_MAX)

    @field_validator("category")
    @classmethod
    def _from_the_list(cls, category: str | None) -> str | None:
        if category is not None and category not in CATEGORIES:
            raise ValueError(f"The category must be one of: {', '.join(CATEGORIES)}.")
        return category

    @field_validator("comment", mode="before")
    @classmethod
    def _no_empty_comment(cls, comment):
        if isinstance(comment, str):
            comment = comment.strip()
        return comment or None

    @model_validator(mode="after")
    def _category_fits_the_verdict(self) -> Review:
        if self.verdict == "problem" and self.category == NO_PROBLEM:
            raise ValueError('A problem cannot have the category "none".')
        if self.verdict != "problem" and self.category is not None:
            raise ValueError("Only an edit called a problem has a category.")
        return self

    @property
    def key(self) -> Key:
        return (self.rev_id, self.system_version)


def _review(prediction: Prediction, verdict: str, category, comment, now) -> Review:
    return Review(
        rev_id=prediction.rev_id,
        system_version=prediction.system_version,
        verdict=verdict,
        category=category,
        decided_at=now or datetime.now(UTC),
        comment=comment,
    )


def problem(
    prediction: Prediction,
    category: str | None = None,
    *,
    comment: str | None = None,
    now: datetime | None = None,
) -> Review:
    """The edit should not stand. `category` is the reviewer's own, not the model's."""
    return _review(prediction, "problem", category, comment, now)


def fine(
    prediction: Prediction, *, comment: str | None = None, now: datetime | None = None
) -> Review:
    """The reviewer sees nothing wrong with the edit."""
    return _review(prediction, "fine", None, comment, now)


def unsure(
    prediction: Prediction, *, comment: str | None = None, now: datetime | None = None
) -> Review:
    """The reviewer cannot tell from what the page shows."""
    return _review(prediction, "unsure", None, comment, now)


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
        """The decision in force for each entry, by (rev_id, system_version).

        The newest `decided_at` wins. Of two made at the same moment, the one written
        later wins.
        """
        newest: dict[Key, Review] = {}
        for review in self.read():
            held = newest.get(review.key)
            if held is None or review.decided_at >= held.decided_at:
                newest[review.key] = review
        return newest

    def for_edit(self, rev_id: int) -> list[Review]:
        """Every decision on one edit, under any system version, in the order written."""
        return [review for review in self.read() if review.rev_id == rev_id]


@dataclass(frozen=True)
class Summary:
    """Counts over a set of decisions."""

    problem: int = 0
    fine: int = 0
    unsure: int = 0
    # Reviewed edits whose queue entry was passed, by the tier that had decided.
    by_model: int = 0
    by_tier1: int = 0
    # Edits the reviewer called a problem whose queue entry was passed, and how many
    # of those stood in the top 10% of their queue.
    problems_placed: int = 0
    problems_in_top: int = 0

    @property
    def decided(self) -> int:
        return self.problem + self.fine + self.unsure

    @property
    def placed(self) -> int:
        """Reviewed edits whose queue entry was passed."""
        return self.by_model + self.by_tier1


def places(predictions: Iterable[Prediction]) -> dict[Key, tuple[int, int]]:
    """Where each entry stands in its queue: (place from 1, length of the queue).

    One queue per system version, ordered as the queue is: riskiest first, ties to
    the earlier revision.
    """
    queues: dict[str, list[tuple[int, float]]] = {}
    for prediction in predictions:
        queues.setdefault(prediction.system_version, []).append(
            (prediction.rev_id, prediction.verdict.risk)
        )
    found: dict[Key, tuple[int, int]] = {}
    for version, rows in queues.items():
        for place, rev_id in enumerate(rank(rows), start=1):
            found.setdefault((rev_id, version), (place, len(rows)))
    return found


def summary(reviews: Iterable[Review], predictions: Iterable[Prediction] | None = None) -> Summary:
    """Count decisions per verdict, per deciding tier, and problems near the top.

    Pass `log.latest().values()` to count the decisions in force. `predictions` is
    the queue that was reviewed, whole: an entry's place is counted among the rows
    passed that share its system version, and the top of a queue is its first 10%,
    rounded up. A decision whose entry is not among them is counted under its verdict
    and left out of the other counts.
    """
    rows = list(predictions or [])
    by_key = {(p.rev_id, p.system_version): p for p in rows}
    standing = places(rows)
    counts = dict.fromkeys(VERDICTS, 0)
    by_model = by_tier1 = placed = in_top = 0
    for review in reviews:
        counts[review.verdict] += 1
        prediction = by_key.get(review.key)
        if prediction is None:
            continue
        if prediction.verdict.source == "model":
            by_model += 1
        else:
            by_tier1 += 1
        if review.verdict == "problem":
            place, length = standing[review.key]
            placed += 1
            in_top += place <= math.ceil(length * TOP_SHARE)
    return Summary(
        problem=counts["problem"],
        fine=counts["fine"],
        unsure=counts["unsure"],
        by_model=by_model,
        by_tier1=by_tier1,
        problems_placed=placed,
        problems_in_top=in_top,
    )
