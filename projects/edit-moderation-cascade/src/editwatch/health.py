"""Feed health: checks that fail the run when the stream cannot be trusted.

A changed event shape, a silent stream or a stream that lags must stop the run and
show on the scoreboard. None of them may pass as a quiet hour.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import Edit
from .stream import StreamReader
from .wiki import FeedError, WikiClient

LISTEN_S = 20.0
# English Wikipedia gets about one article edit by a person a second. Far fewer is a fault.
MIN_EDITS_PER_S = 0.2
MIN_EVENTS_PER_S = 1.0
MAX_LAG_S = 120.0


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def check_rate(events: int, kept: int, seconds: float) -> list[Check]:
    return [
        Check(
            "stream_volume",
            events / seconds >= MIN_EVENTS_PER_S,
            f"{events} events in {seconds:.0f} seconds across all wikis "
            f"(at least {MIN_EVENTS_PER_S:g} a second expected).",
        ),
        Check(
            "edits_kept",
            kept / seconds >= MIN_EDITS_PER_S,
            f"{kept} article edits by people in {seconds:.0f} seconds "
            f"(at least {MIN_EDITS_PER_S:g} a second expected).",
        ),
    ]


def check_lag(edits: list[Edit], now: datetime) -> Check:
    stamped = [e.event_time for e in edits if e.event_time is not None]
    if not stamped:
        return Check("stream_lag", False, "No event carried its own time.")
    lag = (now - max(stamped)).total_seconds()
    return Check(
        "stream_lag",
        lag <= MAX_LAG_S,
        f"The newest event is {lag:.0f} seconds old (limit {MAX_LAG_S:.0f}).",
    )


def run_checks(
    reader: StreamReader,
    client: WikiClient,
    seconds: float = LISTEN_S,
    now: datetime | None = None,
    clock=time.monotonic,
) -> dict:
    """Listen to the stream briefly, then ask the wiki about the newest edit heard."""
    checks: list[Check] = []
    edits: list[Edit] = []
    deadline = clock() + seconds
    try:
        for edit in reader.edits(stop=lambda: clock() >= deadline):
            edits.append(edit)
        checks.append(Check("stream_answers", True, "The stream delivered events that parse."))
    except FeedError as exc:
        checks.append(Check("stream_answers", False, str(exc)))
    moment = now or datetime.now(UTC)
    if checks[0].ok:
        checks += check_rate(reader.events, reader.kept, seconds)
        checks.append(check_lag(edits, moment))
    if edits:
        try:
            tags = client.revision_tags([edits[-1].rev_id])
            found = tags.get(edits[-1].rev_id) is not None
            checks.append(
                Check("wiki_answers", found, "The wiki returned the tags of the newest edit."
                      if found else "The wiki did not return the newest edit.")
            )  # fmt: skip
        except FeedError as exc:
            checks.append(Check("wiki_answers", False, str(exc)))
    return {
        "ok": all(check.ok for check in checks),
        "checked_at": moment.isoformat(timespec="seconds"),
        "checks": [asdict(check) for check in checks],
    }


def write(result: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")


def read(path: Path) -> dict | None:
    return json.loads(path.read_text("utf-8")) if path.exists() else None
