"""The demo: the desk on a small tracker written for this repository.

The fixture is 84 invented issues of a fictional game engine. Because the text is
ours, it can be committed, and so can the model's recorded answers on it. The demo,
the tests and CI run on it without a key and without calling GitHub.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from .backtest import build_desk, window
from .config import GroupConfig, Settings
from .embed import Embedder
from .evals import Score, families_from, score
from .llm import LLMProvider
from .models import Issue, Outcome, Prediction, TimelineEvent
from .pipeline import Desk, process_issue
from .queue import QueueItem, build_item
from .store import Store
from .truth import collect

FIXTURE_REPO = "example/larkspur"
# When the demo's answers are stamped. Fixed, so a rerun writes the same rows.
FIXTURE_NOW = datetime(2026, 4, 11, 8, 0, tzinfo=UTC)


def fixture_settings(home: Path) -> Settings:
    """The fictional tracker's configuration: two label groups, a cut on 1 April."""
    return Settings(
        repo=FIXTURE_REPO,
        groups=(
            GroupConfig("area", "topic:", 8, "The part of the engine the problem is in."),
            GroupConfig(
                "platform",
                "platform:",
                4,
                "Only when the problem is specific to that platform.",
            ),
        ),  # fmt: skip
        keywords={"topic:scripting": ("larkscript", "script")},
        history_start=date(2026, 1, 1),
        cut=date(2026, 4, 1),
        held_out_start=date(2026, 4, 1),
        test_end=date(2026, 4, 30),
        duplicate_confident=0.90,
        duplicate_propose=0.80,
        home=home,
    )


def load_fixture(fixture_dir: Path) -> tuple[list[Issue], dict[int, list[TimelineEvent]]]:
    issues = [
        Issue.model_validate(json.loads(line))
        for line in (fixture_dir / "issues.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    raw = json.loads((fixture_dir / "timelines.json").read_text("utf-8"))
    timelines = {
        int(number): [TimelineEvent.model_validate(e) for e in events]
        for number, events in raw.items()
    }
    return issues, timelines


def fixture_store(fixture_dir: Path) -> Store:
    """The fixture in a store held in memory, as if it had just been fetched."""
    issues, timelines = load_fixture(fixture_dir)
    store = Store(":memory:")
    store.add_issues(issues, seen_at=FIXTURE_NOW)
    for number, events in timelines.items():
        store.set_timeline(number, events)
    return store


def fixture_desk(
    settings: Settings, embedder: Embedder, provider: LLMProvider | None = None
) -> Desk:
    """A desk trained on the fixture. Training on 72 issues takes a moment; nothing is cached."""
    store = fixture_store(settings.fixture_dir)
    return build_desk(settings, store, embedder, cut=settings.cut, provider=provider, cache=False)


def run_demo(
    desk: Desk, configs: tuple[str, ...]
) -> tuple[list[Prediction], dict[int, Outcome], list[QueueItem], list[Score]]:
    """Answer the fixture's new arrivals, read their outcomes and score the answers."""
    settings = desk.settings
    arrivals = window(desk.store, settings.cut, settings.test_end)
    outcomes = {
        o.number: o for o in collect(desk.store, desk.taxonomy, desk.store.issues(), FIXTURE_NOW)
    }
    predictions: list[Prediction] = []
    for config in configs:
        for issue in arrivals:
            prediction, note = process_issue(desk, issue, config, now=FIXTURE_NOW)
            if note:
                desk.store.set_note(issue.number, prediction.system_version, note)
            predictions.append(prediction)
    shown = configs[-1]
    items = [
        item
        for p in predictions
        if p.config == shown
        and (item := build_item(desk.store, p, outcomes.get(p.number))) is not None
    ]
    arrival_outcomes = {i.number: outcomes[i.number] for i in arrivals}
    scores = score(
        predictions, arrival_outcomes, desk.taxonomy, desk.primary_group, families_from(outcomes)
    )
    return predictions, arrival_outcomes, items, scores
