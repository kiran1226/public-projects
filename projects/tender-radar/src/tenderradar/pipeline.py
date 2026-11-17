"""One day of tenders in, one list per profile out.

`process_day` is the only place lists are made. The live run calls it with today's
feed and the backtest calls it with stored history, one day at a time in date order,
so the numbers in the evaluation come from the code that runs every morning.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Literal

from .filters import apply_filters
from .models import GatedAssessment, Notice, Profile, Shortlist, ShortlistEntry
from .rank import Ranker, rank

# Returns the gated assessment, or None when the model was not asked (cap reached) or
# did not answer. The list is written either way.
Assess = Callable[[Profile, Notice], GatedAssessment | None]

_DECISION_ORDER = {"bid": 0, "ask_person": 1, None: 2, "skip": 3}


def keep_and_count(
    profile: Profile, notices: Sequence[Notice], today: date
) -> tuple[list[Notice], dict[str, int]]:
    """Apply the hard rules. A removed notice is counted under the first rule it broke."""
    kept: list[Notice] = []
    removed: Counter[str] = Counter()
    for notice in notices:
        hits = apply_filters(profile, notice, today)
        if hits:
            removed[hits[0].rule] += 1
        else:
            kept.append(notice)
    return kept, dict(sorted(removed.items()))


def order_with_assessments(entries: list[ShortlistEntry]) -> list[ShortlistEntry]:
    """Put what the model would bid on first and what it would skip last.

    Entries the model did not read keep the ranker's order, between the two groups.
    """
    ordered = sorted(
        entries,
        key=lambda e: (
            _DECISION_ORDER[e.assessment.decision if e.assessment else None],
            -(e.assessment.fit if e.assessment else 0),
            e.rank,
        ),
    )
    return [
        entry.model_copy(update={"rank": position}) for position, entry in enumerate(ordered, 1)
    ]


def process_day(
    day: date,
    notices: Sequence[Notice],
    profiles: Sequence[Profile],
    rankers: Sequence[Ranker],
    *,
    versions: dict[str, str],
    mode: Literal["backtest", "live"],
    now: datetime,
    list_size: int = 15,
    assess: Assess | None = None,
    assess_ranker: str | None = None,
    assess_top: int = 10,
    assess_name: str = "fused+model",
    already_written: Callable[[str], bool] = lambda _key: False,
) -> list[Shortlist]:
    """Write one list per profile per ranker for the tenders first published on `day`."""
    by_number = {n.publication_number: n for n in notices}
    out: list[Shortlist] = []
    for profile in profiles:
        kept, removed = keep_and_count(profile, notices, day)
        for ranker in rankers:
            base = dict(
                profile_id=profile.profile_id,
                day=day,
                mode=mode,
                written_at=now,
                candidates=len(kept),
                removed=removed,
            )
            scored = rank(ranker, profile, kept)[:list_size]
            entries = [
                ShortlistEntry(
                    rank=position,
                    publication_number=item.publication_number,
                    score=item.score,
                )
                for position, item in enumerate(scored, 1)
            ]
            plain = Shortlist(
                ranker=ranker.name, system_version=versions[ranker.name], entries=entries, **base
            )
            if not already_written(plain.key):
                out.append(plain)
            if assess is None or ranker.name != assess_ranker:
                continue
            assessed_list = Shortlist(
                ranker=assess_name, system_version=versions[assess_name], **base
            )
            if already_written(assessed_list.key):
                # The model was already paid for this list; a rerun must not pay twice.
                continue
            assessed: list[ShortlistEntry] = []
            skipped = 0
            cost = 0.0
            for entry in entries:
                if entry.rank > assess_top:
                    assessed.append(entry)
                    continue
                result = assess(profile, by_number[entry.publication_number])
                if result is None:
                    skipped += 1
                    assessed.append(entry)
                else:
                    cost += result.cost_usd
                    assessed.append(entry.model_copy(update={"assessment": result}))
            assessed_list.entries = order_with_assessments(assessed)
            assessed_list.skipped_assessments = skipped
            assessed_list.cost_usd = round(cost, 6)
            out.append(assessed_list)
    return out
