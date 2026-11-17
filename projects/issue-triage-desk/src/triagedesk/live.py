"""The live run: answer issues that have just arrived, and later read what became of them.

`run_live` is what the schedule calls every hour. It fetches issues opened since the
last run, stores each as first seen and passes it to `process_issue`, the same
function the backtest replays history through.

A prediction is only worth scoring if the answer was not on the page yet. Maintainers
label most issues within hours, so each row records whether the tracker already
showed a label of the taxonomy, or a close, when the desk first saw the issue. The
scorer leaves those rows out.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .act import Writer
from .budget import Budget
from .config import Settings
from .feed import GitHubClient
from .log import PredictionLog
from .models import Issue
from .pipeline import PROMPT_OF, Desk, process_issue
from .queue import build_item
from .store import Store
from .truth import OutcomeLog, collect, fetch_timelines

LIVE_CONFIGS = ("rules", "classifier", "model")
# The first run looks this far back; later runs start where the last one stopped.
FIRST_LOOKBACK = timedelta(hours=24)
# Each run re-reads this much before its cursor, in case the search index lagged.
OVERLAP = timedelta(hours=2)
CURSOR = "live:newest_created"

Progress = Callable[[str], None]


def live_paths(settings: Settings) -> dict[str, Path]:
    return {
        "log": settings.log_dir / "live.jsonl",
        "outcomes": settings.log_dir / "outcomes-live.jsonl",
        "runs": settings.log_dir / "live-runs.jsonl",
    }


def spent_today(log: PredictionLog, now: datetime) -> float:
    """What the live log shows was spent on the model today (UTC)."""
    today = now.astimezone(UTC).date()
    return sum(p.cost_usd for p in log.read() if p.written_at.astimezone(UTC).date() == today)


def fetch_new(
    settings: Settings,
    store: Store,
    client: GitHubClient,
    now: datetime,
    answered: set[int] | None = None,
) -> tuple[list[Issue], int]:
    """Issues the desk has not answered yet, oldest first, and how many the feed returned.

    "Not answered" is read from the live log, not from the store: an issue a backfill
    brought in an hour ago is in the store and still has no answer.
    """
    cursor = store.get_state(CURSOR)
    start = datetime.fromisoformat(cursor) - OVERLAP if cursor else now - FIRST_LOOKBACK
    fetched = list(client.issues_created(start, now))
    if answered is None:
        answered = store.numbers()
    fresh = [issue for issue in fetched if issue.number not in answered]
    # Known issues get their labels and state refreshed; new ones are stored as seen now.
    store.add_issues(fetched, seen_at=now)
    if fetched:
        newest = max(issue.created_at for issue in fetched)
        if not cursor or newest > datetime.fromisoformat(cursor):
            store.set_state(CURSOR, newest.isoformat())
    return sorted(fresh, key=lambda i: (i.created_at, i.number)), len(fetched)


def run_live(
    desk: Desk,
    client: GitHubClient,
    *,
    now: datetime | None = None,
    configs: tuple[str, ...] = LIVE_CONFIGS,
    budget: Budget | None = None,
    writer: Writer | None = None,
    progress: Progress = lambda line: None,
) -> dict:
    """One scheduled run. Returns a summary, which is also appended to the run log.

    Without a `writer` the run is a shadow run: the log is the only thing written.
    With one, the answers of the last configuration are applied to the tracker.
    """
    now = now or datetime.now(UTC)
    settings = desk.settings
    paths = live_paths(settings)
    log = PredictionLog(paths["log"])
    answered = {number for number, _, _ in log.keys()}
    fresh, fetched = fetch_new(settings, desk.store, client, now, answered)
    desk.index.refresh()
    usable = tuple(c for c in configs if c not in PROMPT_OF or PROMPT_OF[c] in desk.triagers)
    predictions = []
    seen_first = 0
    for issue in fresh:
        _, labels_first_seen, state_first_seen = desk.store.first_seen(issue.number)
        seen_labels = bool(desk.taxonomy.keep(labels_first_seen))
        seen_closed = state_first_seen == "closed"
        seen_first += seen_labels or seen_closed
        for config in usable:
            prediction, note = process_issue(
                desk, issue, config, now=now, seen_labels=seen_labels, seen_closed=seen_closed
            )
            if note:
                desk.store.set_note(issue.number, prediction.system_version, note)
            predictions.append(prediction)
    # Rows the log does not hold yet. Only these may be acted on: a rerun must not
    # label or comment twice.
    new_rows = [p for p in predictions if not log.has(*p.key)]
    written = log.append(predictions)
    applied = 0
    if writer is not None and usable:
        for prediction in new_rows:
            if prediction.config != usable[-1]:
                continue
            item = build_item(desk.store, prediction)
            if item is not None and not item.plan.empty:
                writer.apply(item.plan)
                applied += 1
    summary = {
        "at": now.isoformat(timespec="seconds"),
        "fetched": fetched,
        "new_issues": len(fresh),
        "already_labelled_or_closed": seen_first,
        "predictions": written,
        "applied": applied,
        "mode": "apply" if writer is not None else "shadow",
        "configs": list(usable),
        "model_configs_skipped": [c for c in configs if c not in usable],
        "cost_usd": round(sum(p.cost_usd for p in predictions), 6),
        "calls_refused_at_cap": budget.refused if budget else 0,
        "model_errors": sum(t.errors for t in desk.triagers.values()),
        "search_quota_left": client.remaining["search"],
    }
    paths["runs"].parent.mkdir(parents=True, exist_ok=True)
    with open(paths["runs"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps(summary, sort_keys=True) + "\n")
    progress(
        f"{len(fresh)} new issues ({seen_first} already labelled or closed), "
        f"{written} predictions, US${summary['cost_usd']:.4f}"
    )
    return summary


def read_runs(settings: Settings) -> list[dict]:
    path = live_paths(settings)["runs"]
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def due_for_truth(settings: Settings, log: PredictionLog, outcomes: OutcomeLog, now: datetime):
    """Creation times of live predictions whose waiting period is over and outcome is open."""
    have = {number for number, o in outcomes.read().items() if o.original_status != "not_fetched"}
    due: dict[int, datetime] = {}
    for prediction in log.read():
        if prediction.number in have:
            continue
        if settings.truth_due(prediction.created_at) <= now:
            due[prediction.number] = prediction.created_at
    return due


def collect_live_truth(
    settings: Settings,
    store: Store,
    client: GitHubClient,
    *,
    now: datetime | None = None,
    progress: Progress = lambda line: None,
) -> dict:
    """Read the outcome of every live prediction that is old enough. Returns counts."""
    from .taxonomy import load

    now = now or datetime.now(UTC)
    paths = live_paths(settings)
    log = PredictionLog(paths["log"])
    outcomes = OutcomeLog(paths["outcomes"])
    due = due_for_truth(settings, log, outcomes, now)
    if not due:
        return {"due": 0, "written": 0, "waiting_for_timeline": 0}
    # One search per creation day brings the labels and state as they are now.
    days = sorted({created.astimezone(UTC).date() for created in due.values()})
    for day in days:
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        refreshed = list(client.issues_created(start, start + timedelta(days=1, seconds=-1)))
        store.add_issues(refreshed, seen_at=now)
    issues = [issue for number in sorted(due) if (issue := store.issue(number)) is not None]
    duplicates = [issue.number for issue in issues if issue.closed_as_duplicate]
    _, missing = fetch_timelines(client, store, duplicates, progress)
    taxonomy_path = settings.home / "data" / "taxonomy-live.json"
    if not taxonomy_path.exists():
        raise ValueError(
            f"{taxonomy_path.name} is missing. `triagedesk run` writes it; run that first."
        )
    taxonomy = load(taxonomy_path)
    written = outcomes.append(collect(store, taxonomy, issues, read_at=now))
    return {"due": len(due), "written": written, "waiting_for_timeline": missing}
