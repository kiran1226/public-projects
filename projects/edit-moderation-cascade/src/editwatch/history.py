"""History for the backtest: slices of the recent-changes list, with outcomes.

English Wikipedia gets about 120,000 article edits a day. Three weeks is 2.5 million,
far more than a backtest needs or a polite client should ask for. So history is
sampled: every `slice_hours` hours, the next `slice_edits` edits, which is a few
minutes of traffic. A slice that is fetched is remembered, so a backfill can resume.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta

from .config import Settings
from .store import Store
from .wiki import REVERTED_TAG, WikiClient


def slice_starts(start: datetime, end: datetime, hours: int) -> Iterator[datetime]:
    cursor = start
    while cursor < end:
        yield cursor
        cursor += timedelta(hours=hours)


def backfill(
    client: WikiClient,
    store: Store,
    settings: Settings,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    progress: Callable[[str], None] = lambda line: None,
) -> dict:
    """Fetch every slice from `start` to `end`. Returns counts."""
    start = start or settings.history_start
    end = end or settings.test_end
    fetched = new = skipped = 0
    for moment in slice_starts(start, end, settings.slice_hours):
        key = f"slice:{moment.isoformat()}"
        if store.get_state(key):
            skipped += 1
            continue
        rows = client.recent_changes(moment, settings.slice_edits, settings.namespace)
        # A slice must not run past the end of the window it belongs to.
        rows = [(edit, tags) for edit, tags in rows if edit.timestamp < end]
        new += store.add_edits(rows, source="history", reverted_tag=REVERTED_TAG)
        fetched += len(rows)
        store.set_state(key, str(len(rows)))
        if rows:
            span = (rows[-1][0].timestamp - rows[0][0].timestamp).total_seconds() / 60
            progress(f"{moment:%Y-%m-%d %H:%M}  {len(rows)} edits in {span:.1f} minutes")
    return {"fetched": fetched, "new": new, "skipped": skipped}
