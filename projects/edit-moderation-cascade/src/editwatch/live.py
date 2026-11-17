"""The live run: read the stream for a window, order the queue, log every answer.

Edits arrive on one thread. The main loop scores each with tier 1 at once, and every
few seconds takes the edits waiting for the model (at most 25) and hands them to a
second thread, which reads what they changed in one request to the wiki. The
answers go to a small pool of model calls. The request has a thread of its own
because it takes seconds, and tier 1 must not wait for it. When the queue between
the tiers is full or the day's budget is used up, an edit keeps its tier-1 score and
the skip is counted. The same loop replays a recording.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from .budget import PacedBudget
from .cascade import NO_MODEL, Admission, Cascade
from .config import Settings
from .evals import percentile
from .loadtest import FETCH_BATCH
from .log import PredictionLog
from .models import Change, Edit, Prediction
from .tier2 import Tier2, Tier2Result

LIVE_CONFIG = "cascade"
# After the window closes, how long edits already admitted may still take.
DRAIN_S = 60.0


def live_paths(settings: Settings) -> dict[str, Path]:
    return {
        "log": settings.log_dir / "live.jsonl",
        "outcomes": settings.log_dir / "outcomes-live.jsonl",
        "runs": settings.log_dir / "live-runs.jsonl",
    }


def spent_today(log: PredictionLog, now: datetime) -> float:
    today = now.astimezone(UTC).date()
    return sum(p.cost_usd for p in log.read() if p.written_at.astimezone(UTC).date() == today)


def seconds_into_day(moment: datetime) -> float:
    m = moment.astimezone(UTC)
    return m.hour * 3600 + m.minute * 60 + m.second


def run_window(
    cascade: Cascade,
    edits: Iterator[Edit],
    *,
    tier2: Tier2 | None,
    fetch: Callable[[list[Edit]], dict[int, Change]],
    log: PredictionLog,
    budget: PacedBudget | None = None,
    pause_s: float | None = None,
    config: str = LIVE_CONFIG,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    on_edit: Callable[[Edit], None] = lambda edit: None,
) -> dict:
    """Process every edit the iterator yields. Returns a summary of the run."""
    settings = cascade.settings
    pause = settings.wiki_pause_s if pause_s is None else pause_s
    version = cascade.version(config)
    admission = Admission(cascade.threshold, settings.queue_bound, budget)
    inbox: queue.Queue = queue.Queue()
    finished = threading.Event()

    def read() -> None:
        try:
            for edit in edits:
                inbox.put((edit, now()))
        except Exception as exc:  # surfaced to the main loop, which fails the run
            inbox.put(exc)
        finally:
            finished.set()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()

    rows: list[Prediction] = []
    written = 0
    saved = 0
    waiting: list[tuple[Edit, float, datetime]] = []
    in_flight: list[tuple[Future, Edit, float, datetime, Change]] = []
    latencies = {"tier1": [], "model": []}
    seen = model_errors = 0
    fetching: tuple[Future, list[tuple[Edit, float, datetime]]] | None = None
    last_fetch_done = float("-inf")
    started = time.monotonic()
    drain_until: float | None = None

    def row(edit: Edit, verdict, arrived: datetime, cost: float = 0.0) -> None:
        done = now()
        latency = max(0.0, (done - (edit.event_time or arrived)).total_seconds())
        latencies["model" if verdict.source == "model" else "tier1"].append(latency)
        rows.append(
            Prediction(
                rev_id=edit.rev_id,
                made_at=edit.timestamp,
                written_at=done,
                system_version=version,
                config=config,
                verdict=verdict,
                cost_usd=cost,
                latency_s=latency,
            )  # fmt: skip
        )

    with (
        ThreadPoolExecutor(max_workers=max(1, settings.workers)) as pool,
        ThreadPoolExecutor(max_workers=1) as fetcher,
    ):
        while True:
            # 1. Everything that has arrived: tier 1 on the whole batch at once (one call
            #    to the classifier costs about as much for fifty edits as for one),
            #    then admit or log each edit.
            batch_in: list[tuple[Edit, datetime]] = []
            while True:
                try:
                    item = inbox.get_nowait()
                except queue.Empty:
                    break
                if isinstance(item, Exception):
                    raise item
                batch_in.append(item)
            scores = cascade.tier1.scores([edit for edit, _ in batch_in]) if batch_in else []
            for (edit, arrived), score in zip(batch_in, scores, strict=True):
                score = float(score)
                seen += 1
                on_edit(edit)
                if tier2 is None:
                    # No model in this run: the edit keeps its tier-1 score, and the
                    # row says so if the edit was in the model's slice.
                    skipped = NO_MODEL if cascade.in_slice(score) else None
                    row(edit, cascade.tier1_verdict(score, skipped), arrived)
                    continue
                reason = admission.offer(score, seconds_into_day(arrived))
                if reason is None:
                    waiting.append((edit, score, arrived))
                else:
                    skipped = None if reason == "below_slice" else reason
                    row(edit, cascade.tier1_verdict(score, skipped), arrived)
            # 2. Model calls that have come back.
            for entry in [e for e in in_flight if e[0].done()]:
                in_flight.remove(entry)
                future, edit, score, arrived, change = entry
                result: Tier2Result = future.result()
                admission.done()
                cost = 0.0
                if result.stats is not None and not result.stats.replayed:
                    cost = result.stats.cost_usd
                    if budget is not None:
                        budget.add(cost)
                if result.answer is None:
                    model_errors += 1
                row(edit, cascade.model_verdict(score, result.answer, change), arrived, cost)
            # 3. The request to the wiki that has come back: on to the model.
            if fetching is not None and fetching[0].done():
                fetched, batch = fetching
                fetching = None
                last_fetch_done = time.monotonic()
                changes = fetched.result()
                for edit, score, arrived in batch:
                    change = changes.get(edit.rev_id) or Change(rev_id=edit.rev_id, available=False)
                    if not change.available:
                        admission.done()
                        row(edit, cascade.model_verdict(score, None, None), arrived)
                        continue
                    future = pool.submit(tier2.read, edit, change)
                    in_flight.append((future, edit, score, arrived, change))
            # 4. The next request, for the edits waiting: one at a time, on its own
            #    thread, and not before the pause since the last answer has passed.
            if fetching is None and waiting and time.monotonic() - last_fetch_done >= pause:
                batch, waiting[:] = waiting[:FETCH_BATCH], waiting[FETCH_BATCH:]
                fetching = (fetcher.submit(fetch, [edit for edit, _, _ in batch]), batch)
            # 5. Done when the stream has ended and nothing is waiting.
            if finished.is_set() and inbox.empty():
                if not waiting and not in_flight and fetching is None:
                    break
                drain_until = drain_until or time.monotonic() + DRAIN_S
                if time.monotonic() > drain_until:
                    for edit, score, arrived in waiting:
                        row(edit, cascade.tier1_verdict(score, "window_closed"), arrived)
                    waiting.clear()
                    if not in_flight and fetching is None:
                        break
            # Save what was decided in this pass, so a run that is cut short keeps its
            # rows and the spend they record.
            if len(rows) > saved:
                written += log.append(rows[saved:])
                saved = len(rows)
            time.sleep(0.05)

    written += log.append(rows[saved:])
    return {
        "at": now().isoformat(timespec="seconds"),
        "seconds": round(time.monotonic() - started, 1),
        "edits": seen,
        "predictions": written,
        "read_by_model": len(latencies["model"]),
        "skipped_queue_full": admission.counts["queue_full"],
        "skipped_budget": admission.counts["budget"],
        "model_errors": model_errors,
        "cost_usd": round(sum(p.cost_usd for p in rows), 6),
        "tier1_p95_s": _round(percentile(latencies["tier1"], 0.95)),
        "model_p50_s": _round(percentile(latencies["model"], 0.50)),
        "model_p95_s": _round(percentile(latencies["model"], 0.95)),
        "model": tier2.model if tier2 is not None else None,
        "version": version,
    }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def append_run(settings: Settings, summary: dict) -> None:
    path = live_paths(settings)["runs"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(summary, sort_keys=True) + "\n")


def read_runs(settings: Settings) -> list[dict]:
    path = live_paths(settings)["runs"]
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
