"""The outcome log: who won, and when the feed said so. Append-only, like the predictions."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from .models import Outcome


class OutcomeLog:
    def __init__(self, path: Path):
        self.path = Path(path)

    def read(self) -> list[Outcome]:
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as handle:
            return [Outcome.model_validate(json.loads(line)) for line in handle if line.strip()]

    def append(self, outcomes: Iterable[Outcome]) -> int:
        """Add outcomes not recorded yet. An outcome keeps its first arrival date."""
        known = {outcome.key for outcome in self.read()}
        fresh = []
        for outcome in outcomes:
            if outcome.key not in known:
                known.add(outcome.key)
                fresh.append(outcome)
        if fresh:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                for outcome in fresh:
                    handle.write(json.dumps(outcome.model_dump(mode="json")) + "\n")
        return len(fresh)
