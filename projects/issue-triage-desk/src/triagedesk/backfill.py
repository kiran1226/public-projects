"""Fetch history: every issue created in a range, month by month, resumable.

A month that is finished is remembered in the store, so an interrupted backfill picks
up where it stopped and a second run of the same range costs no requests.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta

from .config import day_start
from .feed import GitHubClient
from .store import Store


def month_ranges(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Calendar months covering [start, end], each as its first and last day in range."""
    cursor = start
    while cursor <= end:
        first_of_next = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1)
        last = min(end, first_of_next - timedelta(days=1))
        yield cursor, last
        cursor = first_of_next


def fetch_range(
    client: GitHubClient,
    store: Store,
    start: date,
    end: date,
    *,
    today: date | None = None,
    newest_first: bool = True,
    refresh: bool = False,
    progress: Callable[[str], None] = lambda line: None,
) -> dict:
    """Store every issue created from `start` to `end`. Returns counts.

    With `refresh`, finished months are fetched again: titles and bodies stay as
    first seen, labels and state are brought up to date. Do this before retraining.
    """
    today = today or datetime.now(UTC).date()
    months = list(month_ranges(start, end))
    if newest_first:
        months.reverse()
    fetched = new = skipped = 0
    for first, last in months:
        key = f"backfill:{client.repo}:{first.isoformat()}:{last.isoformat()}"
        if store.get_state(key) and not refresh:
            skipped += 1
            continue
        window_end = day_start(last) + timedelta(days=1) - timedelta(seconds=1)
        batch = list(client.issues_created(day_start(first), window_end))
        added = store.add_issues(batch)
        fetched += len(batch)
        new += added
        # A month that is still running, or only just over, is fetched again next time.
        if last < today - timedelta(days=1):
            store.set_state(key, str(len(batch)))
        progress(
            f"{first:%Y-%m} {len(batch):>5} issues, {added:>5} new "
            f"(search quota left: {client.remaining['search']})"
        )
    return {"months": len(months), "skipped": skipped, "fetched": fetched, "new": new}
