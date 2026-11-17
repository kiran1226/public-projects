"""Feed health: is the feed answering, in the shape expected, with a normal day's volume.

A feed that breaks quietly is worse than one that breaks loudly. These checks turn a
quiet break (an empty day, a renamed field, a stale feed) into a failed run.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

# A publication day with fewer notices than this share of a usual day is flagged.
LOW_VOLUME_SHARE = 0.3
# TED does not publish on weekends or EU holidays; four days covers a long weekend.
STALE_AFTER_DAYS = 4
MAX_PARSE_FAILURE_SHARE = 0.01
VOLUME_HISTORY_DAYS = 20


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Health:
    checked_at: str
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def to_json(self) -> dict:
        return {
            "ok": self.ok,
            "checked_at": self.checked_at,
            "checks": [asdict(c) for c in self.checks],
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=1) + "\n", encoding="utf-8")


def check_reachable(error: str | None) -> Check:
    if error:
        return Check("feed_answers", False, error)
    return Check("feed_answers", True, "the feed answered every request")


def check_parsing(fetched: int, failures: int) -> Check:
    if fetched == 0:
        return Check("records_parse", True, "no records fetched")
    share = failures / fetched
    return Check(
        "records_parse",
        share <= MAX_PARSE_FAILURE_SHARE,
        f"{failures} of {fetched} records could not be read",
    )


def check_freshness(latest_day: date | None, today: date) -> Check:
    if latest_day is None:
        return Check("feed_is_fresh", False, "no notices in the store")
    age = (today - latest_day).days
    return Check(
        "feed_is_fresh",
        age <= STALE_AFTER_DAYS,
        f"latest notices were published {latest_day}, {age} days ago",
    )


def check_volume(counts_by_day: dict[date, int]) -> Check:
    """Compare the latest publication day with the days before it."""
    if len(counts_by_day) < 6:
        return Check("volume_is_usual", True, "too few days in the store to compare")
    days = sorted(counts_by_day)
    latest = days[-1]
    earlier = [counts_by_day[day] for day in days[-(VOLUME_HISTORY_DAYS + 1) : -1]]
    usual = statistics.median(earlier)
    count = counts_by_day[latest]
    return Check(
        "volume_is_usual",
        count >= LOW_VOLUME_SHARE * usual,
        f"{count} notices on {latest}; a usual day has about {usual:g}",
    )


def assess_health(
    *,
    now: datetime,
    today: date,
    feed_error: str | None,
    fetched: int,
    parse_failures: int,
    latest_day: date | None,
    counts_by_day: dict[date, int],
) -> Health:
    health = Health(checked_at=now.isoformat(timespec="seconds"))
    health.checks.append(check_reachable(feed_error))
    if feed_error is None:
        health.checks.append(check_parsing(fetched, parse_failures))
        health.checks.append(check_freshness(latest_day, today))
        health.checks.append(check_volume(counts_by_day))
    return health
