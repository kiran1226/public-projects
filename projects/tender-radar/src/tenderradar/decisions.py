"""The bid manager's decisions: bid or skip, with a reason, for a tender on the list.

Append-only JSON lines, like the prediction log. A later decision on the same tender
supersedes the earlier one when the log is read, but the earlier line stays in the
file, so a change of mind is on record.

The default file is `data/corrections/decisions.jsonl`. The repository ignores
`data/corrections/`, so decisions made while trying the demo are not published;
commit them in a real deployment.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .config import PROJECT_ROOT, Settings

DEFAULT_PATH = PROJECT_ROOT / "data" / "corrections" / "decisions.jsonl"
CHOICES = ("bid", "skip")


def path_for(settings: Settings) -> Path:
    """The decision file that belongs to these settings: next to the logs.

    With the defaults this is DEFAULT_PATH. When TENDERRADAR_HOME moves the logs, the
    decisions move with them.
    """
    return settings.log_dir.parent / "corrections" / "decisions.jsonl"


class Decision(BaseModel):
    """One decision on one tender."""

    publication_number: str
    # The publication day of the list the tender was on.
    day: date
    decision: Literal["bid", "skip"]
    reason: str = ""
    decided_at: datetime


class DecisionLog:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)

    def record(
        self,
        publication_number: str,
        day: date,
        decision: str,
        reason: str = "",
        *,
        now: datetime | None = None,
    ) -> Decision:
        """Append one decision. A skip needs a reason; a bid may have one."""
        reason = (reason or "").strip()
        if decision not in CHOICES:
            raise ValueError(f"a decision is 'bid' or 'skip', not {decision!r}")
        if decision == "skip" and not reason:
            raise ValueError("a skip needs a reason")
        made = Decision(
            publication_number=publication_number,
            day=day,
            decision=decision,
            reason=reason,
            decided_at=now or datetime.now(UTC),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(made.model_dump(mode="json"), ensure_ascii=False) + "\n")
        return made

    def all(self) -> list[Decision]:
        """Every decision ever recorded, in the order it was written."""
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as handle:
            return [Decision.model_validate(json.loads(line)) for line in handle if line.strip()]

    def latest(self) -> dict[str, Decision]:
        """The decision in force for each tender: the last one written for it."""
        return {made.publication_number: made for made in self.all()}
