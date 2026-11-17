"""Record a slice of the stream to a file, and play a recording back.

A recording holds what the desk keeps of each event: no account names. Played back,
it goes through the same pipeline as the live stream, at its real pace or faster.
That is how the load test asks "what if ten times as many edits arrived".
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from .models import Edit
from .stream import StreamReader


def record(
    reader: StreamReader,
    path: Path,
    seconds: float,
    *,
    clock: Callable[[], float] = time.monotonic,
    progress: Callable[[str], None] = lambda line: None,
) -> int:
    """Write the edits of the next `seconds` seconds to `path`. Returns how many."""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = clock() + seconds
    written = 0
    with open(path, "w", encoding="utf-8") as handle:
        for edit in reader.edits(stop=lambda: clock() >= deadline):
            handle.write(edit.model_dump_json() + "\n")
            handle.flush()
            written += 1
            if written % 100 == 0:
                progress(f"{written} edits recorded")
    return written


def load(path: Path) -> list[Edit]:
    """The edits of a recording, in the order they arrived."""
    with open(path, encoding="utf-8") as handle:
        return [Edit.model_validate(json.loads(line)) for line in handle if line.strip()]


def arrival_offsets(edits: list[Edit]) -> list[float]:
    """Seconds from the first event to each event, by the stream's own clock."""
    if not edits:
        return []
    moments = [(e.event_time or e.timestamp) for e in edits]
    first = moments[0]
    # The stream is not perfectly ordered; an offset never runs backwards here.
    offsets, latest = [], 0.0
    for moment in moments:
        latest = max(latest, (moment - first).total_seconds())
        offsets.append(latest)
    return offsets


def replay(
    edits: list[Edit],
    speed: float = 1.0,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[Edit]:
    """Yield the edits with their recorded gaps, divided by `speed`."""
    if speed <= 0:
        raise ValueError("speed must be positive")
    started = clock()
    for edit, offset in zip(edits, arrival_offsets(edits), strict=True):
        wait = offset / speed - (clock() - started)
        if wait > 0:
            sleep(wait)
        yield edit
