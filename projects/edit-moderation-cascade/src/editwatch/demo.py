"""The demo: the cascade on a small wiki written for this repository.

The fixture is 300 invented edits to invented articles. Because the text is ours, it
can be committed, and so can the model's recorded answers on it. The demo, the tests
and CI run on it without a key and without touching Wikipedia.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .cascade import NO_MODEL, Cascade
from .combine import DEFAULT
from .config import Settings
from .diff import mark_pairs
from .evals import Score, score
from .models import Change, Edit, Prediction
from .tier1 import Tier1, train
from .tier2 import Tier2

FIXTURE_NOW = datetime(2026, 3, 25, 8, 0, tzinfo=UTC)
# The fixture is small, so the model reads a larger share of it than of the real stream.
FIXTURE_SHARE = 0.40


def load_fixture(fixture_dir: Path) -> tuple[list[Edit], dict[int, bool], dict[int, Change]]:
    """Every fixture edit, its outcome, and the change of each arrival."""
    edits: list[Edit] = []
    reverted: dict[int, bool] = {}
    changes: dict[int, Change] = {}
    for line in (fixture_dir / "edits.jsonl").read_text("utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        lines = row.pop("lines", None)
        reverted[row["rev_id"]] = bool(row.pop("reverted"))
        edit = Edit.model_validate(row)
        edits.append(edit)
        if lines is not None:
            changes[edit.rev_id] = Change(rev_id=edit.rev_id, lines=mark_pairs(lines))
    return edits, reverted, changes


def fixture_tier1(settings: Settings) -> Tier1:
    """Tier 1 trained on the fixture's history. A fraction of a second."""
    edits, reverted, changes = load_fixture(settings.fixture_dir)
    history = [e for e in edits if e.rev_id not in changes]
    return train(history, reverted)


def run_demo(
    settings: Settings, tier2: Tier2 | None
) -> tuple[list[tuple[Edit, Prediction, Change]], dict[int, bool], list[Score]]:
    """Order the fixture's arrivals. Returns the queue (riskiest first) and the scores."""
    edits, reverted, changes = load_fixture(settings.fixture_dir)
    arrivals = [e for e in edits if e.rev_id in changes]
    tier1 = fixture_tier1(settings)
    cascade = Cascade(
        settings, tier1, model=tier2.model if tier2 else None, combiner=DEFAULT,
        share=FIXTURE_SHARE,
    )  # fmt: skip
    rows: list[Prediction] = []
    for config in ("tier1", "cascade"):
        version = cascade.version(config)
        for edit in arrivals:
            s = tier1.score(edit)
            verdict = cascade.tier1_verdict(s)
            cost = 0.0
            if config == "cascade" and tier2 is None and cascade.in_slice(s):
                verdict = cascade.tier1_verdict(s, NO_MODEL)
            if config == "cascade" and tier2 is not None and cascade.in_slice(s):
                result = tier2.read(edit, changes[edit.rev_id])
                verdict = cascade.model_verdict(s, result.answer, changes[edit.rev_id])
                cost = result.stats.cost_usd if result.stats else 0.0
            rows.append(
                Prediction(
                    rev_id=edit.rev_id,
                    made_at=edit.timestamp,
                    written_at=FIXTURE_NOW,
                    system_version=version,
                    config=config,
                    verdict=verdict,
                    cost_usd=cost,
                )  # fmt: skip
            )
    by_id = {e.rev_id: e for e in arrivals}
    final = [p for p in rows if p.config == "cascade"]
    final.sort(key=lambda p: (-p.verdict.risk, p.rev_id))
    queue = [(by_id[p.rev_id], p, changes[p.rev_id]) for p in final]
    outcomes = {e.rev_id: reverted[e.rev_id] for e in arrivals}
    return queue, outcomes, score(rows, outcomes)
