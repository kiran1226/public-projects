"""Truth: what the maintainers did with an issue, read after the waiting period.

Labels and the close reason come with the issue itself. The issue a duplicate was
closed against is not a field anywhere: maintainers write it in a comment
("Duplicate of #124570"). So for issues closed as duplicates the timeline is read
and the reference is parsed. Where no reference can be found, the outcome says so
and the issue is left out of the duplicate scores and counted.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .feed import GitHubClient, RateLimited
from .models import Issue, Outcome, Taxonomy, TimelineEvent
from .store import Store

RESOLVED, UNRESOLVED, OUTSIDE, NOT_FETCHED = "resolved", "unresolved", "outside", "not_fetched"
# A comment this close before the close is taken as its explanation.
CLOSING_COMMENT_WINDOW = timedelta(minutes=30)


def resolve_original(
    number: int, events: list[TimelineEvent], is_older_issue: Callable[[int], bool]
) -> tuple[int | None, str]:
    """The issue a duplicate was closed against, from its timeline.

    Looked for in this order, and the first rule that answers wins:

    1. The last comment before the close that speaks of a duplicate and names an
       older issue.
    2. A comment made within half an hour before the close that names exactly one
       older issue.
    3. The comments, up to a day after the close, name exactly one older issue
       between them.

    If no older issue of this tracker is named and an issue of another repository
    is, the answer is "outside". Two or more older issues with nothing to choose
    between them is "unresolved": the desk does not guess what the truth is.
    """
    closes = [e for e in events if e.event == "closed" and e.state_reason == "duplicate"]
    closed_at = closes[-1].created_at if closes else None
    comments = [e for e in events if e.event == "commented"]

    def older(refs: list[int]) -> list[int]:
        return [ref for ref in refs if ref != number and is_older_issue(ref)]

    def until(limit: timedelta) -> list[TimelineEvent]:
        if closed_at is None:
            return comments
        return [e for e in comments if e.created_at is None or e.created_at <= closed_at + limit]

    # A few seconds of grace: the closing comment and the close share one click.
    before_close = until(timedelta(seconds=30))
    for comment in reversed(before_close):
        if comment.says_duplicate and older(comment.refs):
            return older(comment.refs)[0], RESOLVED
    if closed_at is not None:
        for comment in reversed(before_close):
            if comment.created_at is None:
                continue
            if closed_at - comment.created_at > CLOSING_COMMENT_WINDOW:
                break
            if len(older(comment.refs)) == 1:
                return older(comment.refs)[0], RESOLVED
    around_close = until(timedelta(days=1))
    named = list(dict.fromkeys(ref for c in around_close for ref in older(c.refs)))
    if len(named) == 1:
        return named[0], RESOLVED
    if not named and any(c.outside_refs for c in around_close):
        return None, OUTSIDE
    return None, UNRESOLVED


def outcome_for(
    issue: Issue,
    taxonomy: Taxonomy,
    read_at: datetime,
    timeline: list[TimelineEvent] | None,
    is_older_issue: Callable[[int], bool],
) -> Outcome:
    outcome = Outcome(
        number=issue.number,
        created_at=issue.created_at,
        read_at=read_at,
        labels=taxonomy.keep(issue.labels),
        state_reason=issue.state_reason if issue.state == "closed" else None,
        duplicate=issue.closed_as_duplicate,
    )
    if outcome.duplicate:
        if timeline is None:
            outcome.original_status = NOT_FETCHED
        else:
            outcome.original, outcome.original_status = resolve_original(
                issue.number, timeline, is_older_issue
            )
    return outcome


def fetch_timelines(
    client: GitHubClient,
    store: Store,
    numbers: Iterable[int],
    progress: Callable[[str], None] = lambda line: None,
) -> tuple[int, int]:
    """Fetch timelines not in the store yet. Stops quietly when the quota runs out.

    Returns (fetched, still missing).
    """
    have = store.timeline_numbers()
    wanted = [n for n in numbers if n not in have]
    fetched = 0
    for number in wanted:
        try:
            store.set_timeline(number, client.timeline(number))
        except RateLimited as exc:
            progress(f"stopped after {fetched} timelines: {exc}")
            break
        fetched += 1
        if fetched % 10 == 0:
            progress(
                f"{fetched} of {len(wanted)} timelines (core quota left: "
                f"{client.remaining['core']})"
            )
    return fetched, len(wanted) - fetched


def collect(
    store: Store,
    taxonomy: Taxonomy,
    issues: Iterable[Issue],
    read_at: datetime | None = None,
) -> list[Outcome]:
    """Outcomes for issues, from the store's current copy and the stored timelines."""
    known = store.numbers()

    def is_older_issue_than(number: int) -> Callable[[int], bool]:
        return lambda ref: ref < number and ref in known

    outcomes = []
    for issue in issues:
        moment = read_at or store.refreshed_at(issue.number) or datetime.now(UTC)
        timeline = store.timeline(issue.number) if issue.closed_as_duplicate else None
        outcomes.append(
            outcome_for(issue, taxonomy, moment, timeline, is_older_issue_than(issue.number))
        )
    return outcomes


class OutcomeLog:
    """Outcomes with the time each was read. A later row for an issue replaces an earlier one.

    That happens for one reason only: a duplicate whose timeline could not be fetched
    yet (not_fetched) is read again when the quota allows.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def read(self) -> dict[int, Outcome]:
        outcomes: dict[int, Outcome] = {}
        if self.path.exists():
            with open(self.path, encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        outcome = Outcome.model_validate(json.loads(line))
                        outcomes[outcome.number] = outcome
        return outcomes

    def append(self, outcomes: Iterable[Outcome]) -> int:
        """Add outcomes that are new, or that settle one left open. Returns how many."""
        known = self.read()
        fresh = []
        for outcome in outcomes:
            earlier = known.get(outcome.number)
            if earlier is not None and not (
                earlier.original_status == NOT_FETCHED and outcome.original_status != NOT_FETCHED
            ):
                continue
            known[outcome.number] = outcome
            fresh.append(outcome)
        if fresh:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                for outcome in fresh:
                    row = outcome.model_dump(mode="json", exclude_none=True)
                    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return len(fresh)
