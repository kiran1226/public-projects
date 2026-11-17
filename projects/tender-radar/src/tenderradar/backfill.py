"""Fetch a range of the feed into the local store, month by month, resumable."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from .feed import (
    AWARD_FIELDS,
    COMPETITION_FIELDS,
    TedClient,
    award_query,
    competition_query,
)
from .store import COMPETITION, Store


def month_ranges(start: date, end: date) -> list[tuple[date, date]]:
    """Split [start, end] into calendar-month pieces."""
    ranges = []
    cursor = start
    while cursor <= end:
        next_month = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1)
        ranges.append((cursor, min(end, next_month - timedelta(days=1))))
        cursor = next_month
    return ranges


def fetch_range(
    client: TedClient,
    store: Store,
    kind: str,
    divisions: tuple[str, ...],
    start: date,
    end: date,
    *,
    today: date | None = None,
    force: bool = False,
    progress: Callable[[str], None] = lambda _line: None,
) -> int:
    """Fetch every notice of `kind` published in [start, end]. Returns records fetched.

    A calendar month that is over and was fetched completely is remembered and not
    fetched again. The current month is always fetched again, because it is still growing.
    """
    today = today or date.today()
    build_query = competition_query if kind == COMPETITION else award_query
    fields = COMPETITION_FIELDS if kind == COMPETITION else AWARD_FIELDS
    fetched = 0
    for piece_start, piece_end in month_ranges(start, end):
        whole_month = (
            piece_start.day == 1 and (piece_end + timedelta(days=1)).day == 1 and piece_end < today
        )
        state_key = f"backfill:{kind}:{piece_start:%Y-%m}:{','.join(divisions)}"
        if whole_month and not force and store.get_state(state_key):
            progress(f"{kind} {piece_start:%Y-%m}: already fetched ({store.get_state(state_key)})")
            continue
        batch: list[dict] = []
        count = 0
        for record in client.search(build_query(divisions, piece_start, piece_end), fields):
            batch.append(record)
            count += 1
            if len(batch) >= 1000:
                store.upsert_raw(kind, batch)
                batch = []
        store.upsert_raw(kind, batch)
        fetched += count
        if whole_month:
            store.set_state(state_key, str(count))
        progress(f"{kind} {piece_start} to {piece_end}: {count} notices")
    return fetched
