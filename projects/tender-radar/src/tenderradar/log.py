"""The prediction log: every list the system wrote, with the time it was written.

Append-only. A row is never rewritten, and writing the same list twice adds nothing.
This file is the evidence: scoring reads it, and nothing else, to decide what the
system said before the outcome was known.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterable, Iterator
from pathlib import Path

from .models import GatedAssessment, Shortlist, ShortlistEntry

# Raised by hand when the pipeline's logic changes in a way the configuration does not show.
PIPELINE_VERSION = 1


def system_version(**parts) -> str:
    """A short hash of everything that shapes a prediction.

    Change a prompt, a model, a ranker setting or the list size and the version
    changes, so the scoreboard shows the new behaviour on its own line.
    """
    payload = json.dumps({"pipeline": PIPELINE_VERSION, **parts}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:8]


def _open(path: Path, mode: str):
    if path.suffix == ".gz":
        return gzip.open(path, mode + "t", encoding="utf-8")
    return open(path, mode, encoding="utf-8")


def to_row(shortlist: Shortlist) -> dict:
    """One log line. Entries are [publication number, score] pairs in list order."""
    row = {
        "profile_id": shortlist.profile_id,
        "day": shortlist.day.isoformat(),
        "ranker": shortlist.ranker,
        "system_version": shortlist.system_version,
        "mode": shortlist.mode,
        "written_at": shortlist.written_at.isoformat(),
        "candidates": shortlist.candidates,
        "removed": shortlist.removed,
        "entries": [[e.publication_number, round(e.score, 4)] for e in shortlist.entries],
    }
    assessments = {
        e.publication_number: e.assessment.model_dump(mode="json")
        for e in shortlist.entries
        if e.assessment is not None
    }
    if assessments:
        row["assessments"] = assessments
    if shortlist.skipped_assessments:
        row["skipped_assessments"] = shortlist.skipped_assessments
    if shortlist.cost_usd:
        row["cost_usd"] = shortlist.cost_usd
    return row


def from_row(row: dict) -> Shortlist:
    assessments = row.get("assessments", {})
    entries = [
        ShortlistEntry(
            rank=position,
            publication_number=number,
            score=score,
            assessment=GatedAssessment.model_validate(assessments[number])
            if number in assessments
            else None,
        )
        for position, (number, score) in enumerate(row.get("entries", []), 1)
    ]
    return Shortlist(
        profile_id=row["profile_id"],
        day=row["day"],
        ranker=row["ranker"],
        system_version=row["system_version"],
        mode=row["mode"],
        written_at=row["written_at"],
        candidates=row.get("candidates", 0),
        removed=row.get("removed", {}),
        entries=entries,
        skipped_assessments=row.get("skipped_assessments", 0),
        cost_usd=row.get("cost_usd", 0.0),
    )


class PredictionLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._keys: set[str] | None = None

    def read(self) -> Iterator[Shortlist]:
        if not self.path.exists():
            return
        with _open(self.path, "r") as handle:
            for line in handle:
                if line.strip():
                    yield from_row(json.loads(line))

    def keys(self) -> set[str]:
        if self._keys is None:
            self._keys = {shortlist.key for shortlist in self.read()}
        return self._keys

    def has(self, shortlist_key: str) -> bool:
        return shortlist_key in self.keys()

    def append(self, shortlists: Iterable[Shortlist]) -> int:
        """Add lists that are not in the log yet. Returns how many were added."""
        known = self.keys()
        fresh = []
        for shortlist in shortlists:
            if shortlist.key not in known:
                known.add(shortlist.key)
                fresh.append(shortlist)
        if not fresh:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _open(self.path, "a") as handle:
            for shortlist in fresh:
                handle.write(json.dumps(to_row(shortlist), ensure_ascii=False) + "\n")
        return len(fresh)

    def cost_on(self, day_iso: str) -> float:
        """What the model cost in lists written on a given UTC day."""
        return sum(
            shortlist.cost_usd
            for shortlist in self.read()
            if shortlist.written_at.date().isoformat() == day_iso
        )
