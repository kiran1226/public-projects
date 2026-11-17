"""Does it keep up? A recorded slice of the stream, replayed faster, on a virtual clock.

The load test is a simulation, and says so. Arrival times are real: they come from a
recording of the stream. The time each step takes is real too: requests to the wiki
and model calls are drawn from latencies measured in this project. What is simulated
is the waiting: one request to the wiki every few seconds for up to 25 edits, a fixed
number of model calls in flight, and a bounded queue between the tiers. The admission
policy is the same object the live run uses.
"""

from __future__ import annotations

import heapq
import random
from dataclasses import dataclass, field

from .budget import PacedBudget
from .cascade import Admission
from .evals import percentile

FETCH_BATCH = 25
# A request for at most this many edits counts as small: its time is drawn from the
# requests of live runs, which carry a few edits each, not from full batches.
SMALL_BATCH = 5


@dataclass
class Latencies:
    """Seconds each step took when it was measured for real."""

    tier1: float = 0.0005
    # A request for a full batch of edits: two revision texts each.
    fetch: list[float] = field(default_factory=lambda: [0.6])
    model: list[float] = field(default_factory=lambda: [1.5])
    # A request for a few edits (at most SMALL_BATCH), as the live loop makes at the
    # stream's own rate. Measured by live runs. Empty: every request draws from `fetch`.
    fetch_small: list[float] = field(default_factory=list)


@dataclass
class LoadResult:
    speed: float
    edits: int
    seconds: float
    # Seconds from arrival to a place in the queue, per edit, by which tier decided.
    tier1_latencies: list[float]
    model_latencies: list[float]
    counts: dict[str, int]
    longest_wait: int
    # How many replays, each with its own draw of request and model times, are pooled
    # here. Latencies are pooled; counts are summed and reported per replay.
    runs: int = 1

    @property
    def rate(self) -> float:
        return self.edits / self.seconds if self.seconds else 0.0

    @property
    def skipped(self) -> int:
        return self.counts.get("queue_full", 0) + self.counts.get("budget", 0)

    def summary(self) -> dict:
        both = self.tier1_latencies + self.model_latencies
        return {
            "speed": self.speed,
            "edits": self.edits,
            "edits_per_second": round(self.rate, 2),
            "replays": self.runs,
            "read_by_model": round(len(self.model_latencies) / self.runs),
            "skipped_queue_full": round(self.counts.get("queue_full", 0) / self.runs),
            "skipped_budget": round(self.counts.get("budget", 0) / self.runs),
            "model_p50_s": _round(percentile(self.model_latencies, 0.50)),
            "model_p95_s": _round(percentile(self.model_latencies, 0.95)),
            "all_p95_s": _round(percentile(both, 0.95)),
            "longest_wait": self.longest_wait,
        }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def simulate(
    offsets: list[float],
    scores: list[float],
    *,
    speed: float,
    threshold: float,
    queue_bound: int,
    workers: int,
    wiki_pause_s: float,
    latencies: Latencies,
    budget: PacedBudget | None = None,
    cost_per_call: float = 0.0,
    day_start_s: float = 0.0,
    seed: int = 17,
) -> LoadResult:
    """Replay arrivals `speed` times faster through the two tiers. Deterministic for a seed."""
    rng = random.Random(seed)
    admission = Admission(threshold=threshold, queue_bound=queue_bound, budget=budget)
    arrivals = [offset / speed for offset in offsets]
    events: list[tuple[float, int, str, object]] = []
    order = 0

    def push(when: float, kind: str, payload: object = None) -> None:
        nonlocal order
        order += 1
        heapq.heappush(events, (when, order, kind, payload))

    for index, when in enumerate(arrivals):
        push(when, "arrive", index)

    to_fetch: list[int] = []
    to_model: list[int] = []
    free_workers = workers
    fetching = False
    fetch_scheduled = False
    last_fetch_end = -1e9
    tier1_done: list[float] = []
    model_done: list[float] = []
    longest = 0

    def schedule_fetch(now: float) -> None:
        nonlocal fetch_scheduled
        if not fetching and not fetch_scheduled and to_fetch:
            fetch_scheduled = True
            # The client waits the pause from its last answer, not from its last request.
            push(max(now, last_fetch_end + wiki_pause_s), "fetch_start")

    def start_models(now: float) -> None:
        nonlocal free_workers
        while free_workers and to_model:
            index = to_model.pop(0)
            free_workers -= 1
            push(now + rng.choice(latencies.model), "model_done", index)

    while events:
        now, _, kind, payload = heapq.heappop(events)
        if kind == "arrive":
            index = payload
            ready = now + latencies.tier1
            reason = admission.offer(scores[index], day_start_s + now)
            if reason is None:
                to_fetch.append(index)
                if budget is not None:
                    budget.add(cost_per_call)
                longest = max(longest, admission.waiting)
                schedule_fetch(ready)
            else:
                tier1_done.append(ready - arrivals[index])
        elif kind == "fetch_start":
            fetch_scheduled = False
            batch, to_fetch[:] = to_fetch[:FETCH_BATCH], to_fetch[FETCH_BATCH:]
            if batch:
                fetching = True
                small = latencies.fetch_small and len(batch) <= SMALL_BATCH
                took = rng.choice(latencies.fetch_small if small else latencies.fetch)
                push(now + took, "fetch_done", batch)
        elif kind == "fetch_done":
            fetching = False
            last_fetch_end = now
            to_model.extend(payload)
            start_models(now)
            schedule_fetch(now)
        elif kind == "model_done":
            index = payload
            free_workers += 1
            admission.done()
            model_done.append(now - arrivals[index])
            start_models(now)
    span = arrivals[-1] - arrivals[0] if len(arrivals) > 1 else 0.0
    return LoadResult(
        speed, len(arrivals), span, tier1_done, model_done, dict(admission.counts), longest
    )


# One replay draws each request and model time at random from the measured ones, and
# its 95th percentile moves by several seconds with the draw. So each speed is
# replayed this many times and the replays are pooled.
REPLAYS = 10


def pooled(results: list[LoadResult]) -> LoadResult:
    first = results[0]
    counts: dict[str, int] = {}
    for result in results:
        for key, value in result.counts.items():
            counts[key] = counts.get(key, 0) + value
    return LoadResult(
        first.speed,
        first.edits,
        first.seconds,
        [x for r in results for x in r.tier1_latencies],
        [x for r in results for x in r.model_latencies],
        counts,
        max(r.longest_wait for r in results),
        runs=len(results),
    )


def sweep(
    offsets: list[float],
    scores: list[float],
    speeds: tuple[float, ...],
    *,
    replays: int = REPLAYS,
    seed: int = 17,
    **kwargs,
) -> list[LoadResult]:
    return [
        pooled([
            simulate(offsets, scores, speed=speed, seed=seed + i, **kwargs)
            for i in range(replays)
        ])
        for speed in speeds
    ]  # fmt: skip


def sustained(results: list[LoadResult], target_s: float) -> LoadResult | None:
    """The fastest replay that stayed inside the latency target and skipped nothing."""
    ok = [
        r
        for r in results
        if r.skipped == 0 and (percentile(r.model_latencies, 0.95) or 0.0) <= target_s
    ]
    return max(ok, key=lambda r: r.speed) if ok else None
