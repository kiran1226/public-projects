"""Truth: was the edit reverted? Read 48 hours after it was made.

MediaWiki marks a revision that was undone with the tag `mw-reverted`. The tag was
checked against page histories before the build (116 of 116 agreed). It is a proxy:
good-faith mistakes are reverted, some reverts are disputes between editors, and
damage nobody noticed counts as fine. The README says so wherever a number is given.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from .config import Settings
from .models import Outcome, Prediction
from .wiki import BATCH, REVERTED_TAG, WikiClient


class OutcomeLog:
    """Outcomes with the time each was read. One row per revision, never rewritten."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def read(self) -> dict[int, Outcome]:
        outcomes: dict[int, Outcome] = {}
        if self.path.exists():
            with open(self.path, encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        outcome = Outcome.model_validate(json.loads(line))
                        outcomes.setdefault(outcome.rev_id, outcome)
        return outcomes

    def append(self, outcomes: Iterable[Outcome]) -> int:
        seen = set(self.read())
        fresh = []
        for outcome in outcomes:
            if outcome.rev_id not in seen:
                seen.add(outcome.rev_id)
                fresh.append(outcome)
        if fresh:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                for outcome in fresh:
                    handle.write(outcome.model_dump_json() + "\n")
        return len(fresh)


def due(
    predictions: list[Prediction], known: dict[int, Outcome], settings: Settings, now: datetime
) -> dict[int, datetime]:
    """Revisions whose waiting period is over and whose outcome has not been read."""
    waiting: dict[int, datetime] = {}
    for prediction in predictions:
        if prediction.rev_id in known:
            continue
        if settings.truth_due(prediction.made_at) <= now:
            waiting[prediction.rev_id] = prediction.made_at
    return waiting


def collect(
    client: WikiClient,
    revisions: dict[int, datetime],
    now: datetime | None = None,
    progress: Callable[[str], None] = lambda line: None,
) -> list[Outcome]:
    """Read the tags of each revision, 50 to a request, and say which were reverted."""
    now = now or datetime.now(UTC)
    ids = sorted(revisions)
    outcomes: list[Outcome] = []
    for start in range(0, len(ids), BATCH):
        chunk = ids[start : start + BATCH]
        tags = client.revision_tags(chunk)
        for rev_id in chunk:
            found = tags.get(rev_id)
            outcomes.append(
                Outcome(
                    rev_id=rev_id,
                    made_at=revisions[rev_id],
                    read_at=now,
                    reverted=found is not None and REVERTED_TAG in found,
                    found=found is not None,
                )
            )
        progress(f"{len(outcomes)} of {len(ids)} outcomes read")
    return outcomes
