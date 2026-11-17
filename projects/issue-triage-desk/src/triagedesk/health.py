"""Feed health: checks that fail the run when the tracker's answers cannot be trusted.

A changed response shape, a silent feed or a new label family must stop the run and
show on the scoreboard. None of them may pass as a quiet hour.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import Settings
from .feed import FeedError, GitHubClient
from .models import Issue, Taxonomy
from .store import Store

FRESH_WITHIN = timedelta(hours=48)
WEEKS_OF_HISTORY = 8
# A week with less than this share of the usual volume is flagged.
LOW_VOLUME_SHARE = 0.4


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def check_fresh(recent: list[Issue], now: datetime) -> Check:
    if not recent:
        return Check("feed_fresh", False, "No issue was opened in the last seven days.")
    newest = max(issue.created_at for issue in recent)
    age = now - newest
    hours = age.total_seconds() / 3600
    return Check(
        "feed_fresh",
        age <= FRESH_WITHIN,
        f"Newest issue opened {hours:.0f} hour{'' if round(hours) == 1 else 's'} ago "
        f"(limit {FRESH_WITHIN.days * 24}).",
    )


def check_volume(recent: list[Issue], store: Store, now: datetime) -> Check:
    week = timedelta(days=7)
    counts = []
    for back in range(1, WEEKS_OF_HISTORY + 1):
        start = now - week * (back + 1)
        counts.append(len(store.issues(start, start + week)))
    usual = statistics.median(counts) if counts else 0
    if usual == 0:
        return Check("volume", True, "No history to compare the week's volume with.")
    share = len(recent) / usual
    return Check(
        "volume",
        share >= LOW_VOLUME_SHARE,
        f"{len(recent)} issues in seven days; the usual week has {usual:.0f}.",
    )


def check_labels(recent: list[Issue], settings: Settings, taxonomy: Taxonomy) -> Check:
    """A label of a configured group that the taxonomy has never seen."""
    known = set(taxonomy.names()) | set(taxonomy.dropped)
    unknown = sorted(
        {
            label
            for issue in recent
            for label in issue.labels
            if settings.group_of(label) is not None and label not in known
        }
    )
    if unknown:
        return Check(
            "labels_known",
            False,
            f"Labels the taxonomy does not know: {', '.join(unknown)}. Rebuild it.",
        )
    return Check("labels_known", True, "Every label in use is known to the taxonomy.")


def run_checks(
    settings: Settings,
    store: Store,
    client: GitHubClient,
    taxonomy: Taxonomy,
    now: datetime | None = None,
) -> dict:
    """Run every check. The first one is the feed answering in a shape that parses."""
    now = now or datetime.now(UTC)
    checks: list[Check] = []
    recent: list[Issue] | None = None
    try:
        recent = list(client.issues_created(now - timedelta(days=7), now))
        checks.append(Check("feed_answers", True, f"Search returned {len(recent)} issues."))
    except FeedError as exc:
        checks.append(Check("feed_answers", False, str(exc)))
    if recent is not None:
        checks.append(check_fresh(recent, now))
        checks.append(check_volume(recent, store, now))
        checks.append(check_labels(recent, settings, taxonomy))
    return {
        "ok": all(check.ok for check in checks),
        "checked_at": now.isoformat(timespec="seconds"),
        "checks": [asdict(check) for check in checks],
    }


def write(result: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")


def read(path: Path) -> dict | None:
    return json.loads(path.read_text("utf-8")) if path.exists() else None
