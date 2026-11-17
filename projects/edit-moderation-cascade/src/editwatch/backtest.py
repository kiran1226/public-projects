"""The backtest: stored history through the same cascade the live run uses.

Tier 1 trains on edits made before the cut. The test week is split into development
days and held-out days. The model tier is run on a random sample of each, because a
model call on every test edit is neither needed nor affordable; every configuration
is then scored on that same sample.
"""

from __future__ import annotations

import pickle
import random
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from .cascade import NO_MODEL, Cascade
from .combine import Combiner, fit
from .config import Settings
from .log import PredictionLog
from .models import Edit, Prediction, Verdict
from .store import Store
from .tier1 import Tier1, train
from .tier2 import Tier2, Tier2Result

Progress = Callable[[str], None]


def _quiet(line: str) -> None:
    pass


def windows(store: Store, settings: Settings) -> dict[str, list[Edit]]:
    """Edits by people, split in time: training, development days, held-out days."""
    s = settings
    return {
        "training": store.edits(s.history_start, s.cut, source="history"),
        "development": store.edits(s.cut, s.held_out_start, source="history"),
        "held_out": store.edits(s.held_out_start, s.test_end, source="history"),
    }


def load_tier1(store: Store, settings: Settings, progress: Progress = _quiet) -> Tier1:
    """Train tier 1 on the training window, or load the model trained on exactly it."""
    training = windows(store, settings)["training"]
    if not training:
        raise ValueError("No stored history before the cut. Run `editwatch backfill` first.")
    key = f"{len(training)}-{training[0].rev_id}-{training[-1].rev_id}"
    path = settings.home / "var" / f"tier1-{key}.pkl"
    if path.exists():
        with open(path, "rb") as handle:
            return pickle.load(handle)
    progress(f"training tier 1 on {len(training)} edits")
    tier1 = train(training, store.reverted())
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        pickle.dump(tier1, handle)
    return tier1


def sample(edits: list[Edit], size: int, seed: int) -> list[Edit]:
    """A random sample at the natural mix, fixed by the seed, in time order."""
    if len(edits) <= size:
        return list(edits)
    chosen = random.Random(seed).sample(sorted(e.rev_id for e in edits), size)
    keep = set(chosen)
    return [e for e in edits if e.rev_id in keep]


def ask_model(
    cascade: Cascade,
    tier2: Tier2,
    store: Store,
    edits: list[Edit],
    scores: dict[int, float],
    *,
    concurrency: int = 4,
    cap_usd: float = 1.0,
    progress: Progress = _quiet,
) -> dict[int, Tier2Result]:
    """The model's answer for every edit of `edits` that is in the cascade's slice.

    The store belongs to this thread, so the changes are read here and only the
    model calls run in the pool. The call stops asking once `cap_usd` is spent.
    """
    jobs = []
    for edit in edits:
        if cascade.in_slice(scores[edit.rev_id]):
            change = store.change(edit.rev_id)
            if change is not None:
                jobs.append((edit, change))

    def one(job) -> Tier2Result:
        if tier2.cost_usd >= cap_usd:
            return Tier2Result(None, error="cap")
        return tier2.read(*job)

    results: dict[int, Tier2Result] = {}
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for done, ((edit, _), result) in enumerate(
            zip(jobs, pool.map(one, jobs), strict=True), start=1
        ):
            results[edit.rev_id] = result
            if done % 100 == 0 or done == len(jobs):
                failed = sum(1 for r in results.values() if r.answer is None)
                progress(
                    f"{tier2.model}: {done} of {len(jobs)} edits, {failed} without an "
                    f"answer, US${tier2.cost_usd:.3f} spent"
                )
    return results


def fit_combiner(
    edits: list[Edit],
    scores: dict[int, float],
    results: dict[int, Tier2Result],
    reverted: dict[int, bool],
) -> Combiner:
    """Fit the two weights on development edits the model answered."""
    rows = [
        (scores[e.rev_id], results[e.rev_id].answer.risk, reverted[e.rev_id])
        for e in edits
        if e.rev_id in results and results[e.rev_id].answer is not None and e.rev_id in reverted
    ]
    return fit([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows])


def write_rows(
    cascade: Cascade,
    config: str,
    store: Store,
    edits: list[Edit],
    scores: dict[int, float],
    results: dict[int, Tier2Result] | None,
    log: PredictionLog,
    *,
    now: datetime | None = None,
    version_extra: str = "",
) -> int:
    """Append one row per edit under `config`. Edits outside the slice keep tier 1."""
    now = now or datetime.now(UTC)
    version = cascade.version(config + version_extra)
    rows = []
    for edit in edits:
        score = scores[edit.rev_id]
        cost = 0.0
        if results is None or not cascade.in_slice(score):
            verdict = cascade.tier1_verdict(score)
        else:
            result = results.get(edit.rev_id)
            if result is None:
                verdict = cascade.tier1_verdict(score, NO_MODEL)
            else:
                verdict = cascade.model_verdict(score, result.answer, store.change(edit.rev_id))
                cost = result.stats.cost_usd if result.stats else 0.0
        rows.append(
            Prediction(
                rev_id=edit.rev_id,
                made_at=edit.timestamp,
                written_at=now,
                system_version=version,
                config=config,
                verdict=verdict,
                cost_usd=cost,
            )  # fmt: skip
        )
    return log.append(rows)


def write_baseline_rows(
    cascade: Cascade,
    edits: list[Edit],
    scores: dict[int, float],
    baseline: dict[int, float | None],
    log: PredictionLog,
    now: datetime | None = None,
) -> int:
    """Rows for Wikimedia's own model, where it gave a score."""
    now = now or datetime.now(UTC)
    rows = [
        Prediction(
            rev_id=e.rev_id,
            made_at=e.timestamp,
            written_at=now,
            system_version="revertrisk-language-agnostic",
            config="wikimedia",
            verdict=Verdict(risk=baseline[e.rev_id], tier1=scores[e.rev_id]),
        )  # fmt: skip
        for e in edits
        if baseline.get(e.rev_id) is not None
    ]
    return log.append(rows)
