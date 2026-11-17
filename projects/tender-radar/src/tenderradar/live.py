"""The live run: today's feed in, today's lists out, and outcomes as awards arrive.

The scheduled job has no copy of the history database. Everything it needs travels in
small committed files: the panel profiles, a table of hashed supplier aliases, and a
day index that remembers which tenders were published on which day.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .awards import SupplierIndex, _id_node, _name_node
from .filters import apply_filters
from .log import PredictionLog
from .models import Award, Notice, Outcome, Profile, Shortlist
from .pipeline import Assess, process_day
from .rank import Ranker

LIVE_PANEL_RANKERS = ("alert", "fused")
MODEL_RANKER = "fused+model"


def alias_hash(node: str) -> str:
    """A supplier's name or number, hashed. The table of these is committed; the names are not."""
    return hashlib.sha256(f"tender-radar-alias:{node}".encode()).hexdigest()[:16]


def export_aliases(index: SupplierIndex, keys: dict[str, str]) -> dict[str, str]:
    """For each panel supplier, every hashed spelling it is known under -> its profile id."""
    wanted = {key: profile_id for profile_id, key in keys.items()}
    aliases: dict[str, str] = {}
    for node in list(index._uf._parent):
        root = index._uf.find(node)
        if root in wanted:
            aliases[alias_hash(node)] = wanted[root]
    return dict(sorted(aliases.items()))


def profile_ids_for(award: Award, aliases: dict[str, str]) -> set[str]:
    """The panel profiles an award names as a winner."""
    found: set[str] = set()
    for winner in award.winners:
        for node in (_name_node(winner, award.buyer_country), _id_node(winner)):
            if node and alias_hash(node) in aliases:
                found.add(aliases[alias_hash(node)])
    return found


def facts_only(notice: Notice) -> Notice:
    """A notice without its text: what the hard rules need, and nothing a reader could read."""
    return notice.model_copy(
        update={"title": "", "description": "", "lots": [], "conditions": [], "buyer_name": None}
    )


class DayIndex:
    """Which tenders were first published on which day. Append-only, one line per day."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def days(self) -> list[date]:
        return [date.fromisoformat(row["day"]) for row in self._rows()]

    def append(self, day: date, notices: Sequence[Notice]) -> bool:
        """Record a day's tenders once. Returns False if the day is already recorded."""
        if day in self.days():
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "day": day.isoformat(),
            "notices": [
                facts_only(n).model_dump(mode="json", exclude_defaults=True) for n in notices
            ],
        }
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True

    def procedures(self) -> dict[str, Notice]:
        """Every recorded tender by procedure id."""
        found: dict[str, Notice] = {}
        for row in self._rows():
            for raw in row["notices"]:
                notice = Notice.model_validate(raw)
                found.setdefault(notice.procedure_id, notice)
        return found

    def known_procedures(self) -> set[str]:
        return set(self.procedures())


def live_outcomes(
    awards: Iterable[Award],
    aliases: dict[str, str],
    day_index: DayIndex,
    profiles: Sequence[Profile],
) -> list[Outcome]:
    """Awards that name a panel supplier as winner of a tender the live run has listed."""
    recorded = day_index.procedures()
    by_id = {profile.profile_id: profile for profile in profiles}
    outcomes: dict[str, Outcome] = {}
    for award in sorted(awards, key=lambda a: (a.publication_date, a.publication_number)):
        notice = recorded.get(award.procedure_id)
        if notice is None:
            continue
        for profile_id in sorted(profile_ids_for(award, aliases)):
            profile = by_id.get(profile_id)
            if profile is None:
                continue
            outcome = Outcome(
                profile_id=profile_id,
                procedure_id=award.procedure_id,
                arrived=award.publication_date,
                publication_number=notice.publication_number,
                day=notice.publication_date,
                filter_hits=[
                    hit.rule for hit in apply_filters(profile, notice, notice.publication_date)
                ],
            )
            outcomes.setdefault(outcome.key, outcome)
    return list(outcomes.values())


@dataclass
class RunReport:
    days: list[date] = field(default_factory=list)
    tenders: int = 0
    lists_written: int = 0
    assessments: int = 0
    skipped_over_cap: int = 0
    model_errors: int = 0
    spent_usd: float = 0.0
    client_lists: list[Shortlist] = field(default_factory=list)


def days_to_process(available: Sequence[date], recorded: Sequence[date], today: date) -> list[date]:
    """Publication days not processed yet, up to yesterday.

    A day is processed once, so it must be complete: today's issue may still be
    arriving when the run starts, and a tender added later would never be listed. The
    morning run therefore lists what was published up to the day before.

    On the first run only the latest complete day is processed: lists for earlier days
    would be written long after those tenders were published.
    """
    complete = sorted(day for day in available if day < today)
    if not complete:
        return []
    if not recorded:
        return [complete[-1]]
    last = max(recorded)
    return [day for day in complete if day > last]


def run_live(
    *,
    new_procedures: Callable[[date], list[Notice]],
    available_days: Sequence[date],
    day_index: DayIndex,
    log: PredictionLog,
    panel: Sequence[Profile],
    client: Profile | None,
    rankers: Sequence[Ranker],
    versions: dict[str, str],
    list_size: int,
    today: date,
    assess: Assess | None = None,
    assess_top: int = 10,
    now: datetime | None = None,
) -> RunReport:
    """Process every publication day not yet in the day index, oldest first."""
    now = now or datetime.now(UTC)
    report = RunReport()
    panel_rankers = [r for r in rankers if r.name in LIVE_PANEL_RANKERS]
    fused = [r for r in rankers if r.name == "fused"]
    already = day_index.known_procedures()
    for day in days_to_process(available_days, day_index.days(), today):
        notices = [n for n in new_procedures(day) if n.procedure_id not in already]
        lists = process_day(
            day,
            notices,
            panel,
            panel_rankers,
            versions=versions,
            mode="live",
            now=now,
            list_size=list_size,
            already_written=log.has,
        )
        if client is not None:
            client_lists = process_day(
                day,
                notices,
                [client],
                fused,
                versions=versions,
                mode="live",
                now=now,
                list_size=list_size,
                assess=assess,
                assess_ranker="fused" if assess else None,
                assess_top=assess_top,
                assess_name=MODEL_RANKER,
                already_written=log.has,
            )
            lists += client_lists
            report.client_lists += client_lists
        report.lists_written += log.append(lists)
        day_index.append(day, notices)
        already.update(n.procedure_id for n in notices)
        report.days.append(day)
        report.tenders += len(notices)
    return report


def recent_window(today: date, days: int = 14) -> tuple[date, date]:
    return today - timedelta(days=days), today
