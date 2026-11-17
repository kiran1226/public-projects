"""The load test on a virtual clock, and the live loop on real threads."""

from __future__ import annotations

import threading
import time

import pytest

from editwatch.budget import PacedBudget
from editwatch.cascade import Cascade
from editwatch.config import Settings
from editwatch.live import append_run, read_runs, run_window, seconds_into_day
from editwatch.loadtest import FETCH_BATCH, SMALL_BATCH, Latencies, simulate, sustained, sweep
from editwatch.log import PredictionLog
from editwatch.models import Change
from editwatch.tier2 import Tier2
from editwatch.wiki import FeedError

from conftest import ScriptedProvider, answer, at, make_edit, make_tier1

FAST = Latencies(tier1=0.001, fetch=[0.5], model=[1.0])


def run(offsets, scores, **kwargs):
    defaults = dict(
        speed=1.0, threshold=0.5, queue_bound=50, workers=4, wiki_pause_s=5.0, latencies=FAST
    )
    defaults.update(kwargs)
    return simulate(offsets, scores, **defaults)


# -- the simulation ------------------------------------------------------------------


def test_an_edit_below_the_slice_is_placed_at_once_and_is_not_a_skip():
    result = run([0.0, 1.0], [0.1, 0.2])
    assert result.tier1_latencies == pytest.approx([0.001, 0.001]) and result.model_latencies == []
    assert result.skipped == 0 and result.counts["tier1_only"] == 2


def test_a_slice_edit_waits_for_the_fetch_and_the_model():
    result = run([0.0], [0.9])
    assert result.model_latencies == [pytest.approx(0.001 + 0.5 + 1.0)]
    assert result.counts["admitted"] == 1


def test_the_next_request_waits_the_pause_from_the_last_answer():
    # Two slice edits half a second apart: the second waits for the next request,
    # which starts five seconds after the first one was answered (at 0.501), as the
    # wiki client does.
    result = run([0.0, 0.5], [0.9, 0.9])
    first, second = sorted(result.model_latencies)
    assert first == pytest.approx(1.501)
    assert second == pytest.approx((0.501 + 5.0) - 0.5 + 0.5 + 1.0, abs=0.01)


def test_one_request_carries_at_most_a_batch_of_edits():
    arrivals = [0.0] + [0.01 * i for i in range(1, FETCH_BATCH + 6)]
    result = run(arrivals, [0.9] * len(arrivals), queue_bound=1000, workers=100)
    late = [x for x in result.model_latencies if x > 9.0]
    assert len(late) == 5, "the edits beyond one batch wait for a third request"


def test_model_calls_in_flight_are_limited_by_the_workers():
    result = run([0.0, 0.001, 0.002], [0.9] * 3, workers=1, wiki_pause_s=0.0)
    assert sorted(round(x, 1) for x in result.model_latencies) == [1.5, 2.5, 3.5]


def test_a_full_queue_skips_and_counts_and_the_edit_keeps_tier_one():
    result = run([0.0, 0.001, 0.002, 0.003], [0.9] * 4, queue_bound=2)
    assert result.counts["queue_full"] == 2 and result.skipped == 2
    assert len(result.model_latencies) == 2 and len(result.tier1_latencies) == 2
    assert result.longest_wait == 2


def test_a_used_up_budget_skips_and_counts():
    budget = PacedBudget(cap_usd=1.0, spent_usd=0.0, headroom_share=0.0)
    result = run([10.0, 20.0, 30.0], [0.9] * 3, budget=budget, cost_per_call=0.001, day_start_s=0.0)
    # At ten seconds into the day the allowance is about a ten-thousandth of the cap.
    assert result.counts["budget"] >= 1
    assert result.counts["admitted"] + result.counts["budget"] == 3


def test_faster_replay_means_the_same_edits_in_less_time():
    offsets = [float(i) for i in range(100)]
    slow, fast = sweep(offsets, [0.1] * 100, (1.0, 10.0), threshold=0.5, queue_bound=50, workers=4,
                       wiki_pause_s=5.0, latencies=FAST)  # fmt: skip
    assert slow.edits == fast.edits == 100
    assert fast.rate == pytest.approx(10 * slow.rate)


def test_the_simulation_is_the_same_twice_for_one_seed():
    offsets = [i * 0.3 for i in range(200)]
    scores = [0.9 if i % 5 == 0 else 0.1 for i in range(200)]
    wide = Latencies(tier1=0.001, fetch=[0.3, 0.6, 0.9], model=[0.8, 1.5, 3.0])
    a = run(offsets, scores, latencies=wide, speed=10.0)
    b = run(offsets, scores, latencies=wide, speed=10.0)
    assert a.summary() == b.summary()
    assert (
        run(offsets, scores, latencies=wide, speed=10.0, seed=3).model_latencies
        != a.model_latencies
    )


def test_sustained_is_the_fastest_replay_inside_the_target_with_nothing_skipped():
    offsets = [i * 0.2 for i in range(600)]
    scores = [0.9 if i % 5 == 0 else 0.1 for i in range(600)]
    results = sweep(offsets, scores, (1.0, 5.0, 50.0), threshold=0.5, queue_bound=20, workers=2,
                    wiki_pause_s=5.0, latencies=FAST)  # fmt: skip
    best = sustained(results, target_s=15.0)
    assert best is not None and best.speed in (1.0, 5.0)
    assert results[-1].skipped > 0, "at fifty times the rate the queue fills"
    assert sustained(results, target_s=0.5) is None


def test_the_summary_has_what_the_report_shows():
    summary = run([0.0, 1.0], [0.9, 0.1]).summary()
    for key in ("speed", "edits", "edits_per_second", "read_by_model", "skipped_queue_full",
                "skipped_budget", "model_p50_s", "model_p95_s", "all_p95_s", "longest_wait"):  # fmt: skip
        assert key in summary


def test_a_request_for_a_few_edits_draws_from_the_times_live_runs_measured():
    both = Latencies(tier1=0.001, fetch=[4.0], model=[1.0], fetch_small=[0.25])
    # One edit: a small request.
    one = run([0.0], [0.9], latencies=both)
    assert one.model_latencies == [pytest.approx(0.001 + 0.25 + 1.0)]
    # Seven edits at once: more than a small request, so the full-batch time.
    assert SMALL_BATCH < 7 <= FETCH_BATCH
    seven = run([0.0] * 7, [0.9] * 7, latencies=both, workers=10)
    assert all(x == pytest.approx(0.001 + 4.0 + 1.0) for x in seven.model_latencies)
    # Nothing measured live: every request draws from the full-batch times.
    assert run([0.0], [0.9]).model_latencies == [pytest.approx(0.001 + 0.5 + 1.0)]


def test_a_sweep_pools_several_replays_and_reports_counts_per_replay():
    offsets = [0.1 * i for i in range(40)]
    spread = Latencies(tier1=0.001, fetch=[0.2, 3.0, 8.0], model=[0.5, 1.0])
    kwargs = dict(threshold=0.5, queue_bound=50, workers=4, wiki_pause_s=5.0, latencies=spread)
    one = sweep(offsets, [0.9] * 40, (1.0,), replays=1, **kwargs)[0]
    ten = sweep(offsets, [0.9] * 40, (1.0,), replays=10, **kwargs)[0]
    assert one.runs == 1 and ten.runs == 10
    assert len(ten.model_latencies) == 10 * len(one.model_latencies) == 400
    assert ten.summary()["read_by_model"] == 40 and ten.summary()["replays"] == 10
    # The replays differ: each draws its own request times.
    assert len({round(x, 3) for x in ten.model_latencies}) > len(set(one.model_latencies))
    again = sweep(offsets, [0.9] * 40, (1.0,), replays=10, **kwargs)[0]
    assert again.model_latencies == ten.model_latencies


# -- the live loop -------------------------------------------------------------------


def cascade_for(tmp_path, **changes) -> Cascade:
    settings = Settings(home=tmp_path, wiki_pause_s=0.0, **changes)
    return Cascade(settings, make_tier1(), model="scripted-model")


class ScoredTier1:
    """Tier 1 whose score for an edit is looked up, so a test can place edits at will."""

    def __init__(self, scores: dict[int, float]):
        self.inner = make_tier1()
        self.scores_by_id = scores
        self.trained_on = self.inner.trained_on
        self.quantiles = self.inner.quantiles

    def score(self, edit) -> float:
        return self.scores_by_id[edit.rev_id]

    def scores(self, edits) -> list[float]:
        return [self.scores_by_id[e.rev_id] for e in edits]

    def threshold(self, share: float) -> float:
        return self.inner.threshold(share)


def window(
    tmp_path, scores: dict[int, float], *, provider=None, fetch=None, budget=None, **changes
):
    cascade = cascade_for(tmp_path, **changes)
    cascade.tier1 = ScoredTier1(scores)
    edits = [make_edit(rev, minute=i) for i, rev in enumerate(scores)]
    log = PredictionLog(tmp_path / "live.jsonl")
    fetched: list[list[int]] = []

    def default_fetch(batch):
        fetched.append([e.rev_id for e in batch])
        return {e.rev_id: Change(rev_id=e.rev_id, lines=["1 + added text"]) for e in batch}

    tier2 = Tier2(provider) if provider is not None else None
    summary = run_window(cascade, iter(edits), tier2=tier2, fetch=fetch or default_fetch, log=log,
                         budget=budget)  # fmt: skip
    return summary, {p.rev_id: p for p in log.read()}, fetched


def test_every_edit_gets_a_row_and_only_the_slice_reaches_the_model(tmp_path):
    provider = ScriptedProvider(lambda s, u: answer(90, "vandalism", 1, 1))
    summary, rows, fetched = window(tmp_path, {1: 0.9, 2: 0.1, 3: 0.95}, provider=provider)
    assert summary["edits"] == 3 and summary["predictions"] == 3
    assert rows[1].verdict.source == "model" and rows[3].verdict.source == "model"
    assert rows[2].verdict.source == "tier1" and rows[2].verdict.skipped is None
    assert sorted(sum(fetched, [])) == [1, 3]
    assert len(provider.calls) == 2 and summary["read_by_model"] == 2


def test_the_models_answer_carries_category_and_lines_into_the_row(tmp_path):
    provider = ScriptedProvider(lambda s, u: answer(90, "vandalism", 1, 1))
    _, rows, _ = window(tmp_path, {1: 0.9}, provider=provider)
    verdict = rows[1].verdict
    assert (verdict.model_risk, verdict.category, verdict.first_line) == (90, "vandalism", 1)
    assert rows[1].cost_usd > 0 and rows[1].latency_s is not None


def test_without_a_model_slice_edits_keep_tier_one_and_say_why(tmp_path):
    summary, rows, fetched = window(tmp_path, {1: 0.9, 2: 0.1})
    assert rows[1].verdict.skipped == "no_model" and rows[2].verdict.skipped is None
    assert fetched == [] and summary["model"] is None


def test_a_full_queue_between_the_tiers_skips_and_counts(tmp_path):
    provider = ScriptedProvider(lambda s, u: answer(80))
    scores = {rev: 0.9 for rev in range(1, 8)}
    summary, rows, _ = window(tmp_path, scores, provider=provider, queue_bound=2)
    skipped = [r for r in rows.values() if r.verdict.skipped == "queue_full"]
    assert summary["skipped_queue_full"] == len(skipped) >= 1
    assert summary["read_by_model"] + len(skipped) == 7


def test_a_used_up_budget_in_the_live_loop_skips_and_counts(tmp_path):
    provider = ScriptedProvider(lambda s, u: answer(80))
    budget = PacedBudget(cap_usd=0.5, spent_usd=0.5)
    summary, rows, _ = window(tmp_path, {1: 0.9, 2: 0.9}, provider=provider, budget=budget)
    assert summary["skipped_budget"] == 2 and not provider.calls
    assert all(r.verdict.skipped == "budget" for r in rows.values())


def test_the_spend_of_live_calls_is_added_to_the_budget(tmp_path):
    provider = ScriptedProvider(lambda s, u: answer(80))
    budget = PacedBudget(cap_usd=5.0)
    summary, _, _ = window(tmp_path, {1: 0.9, 2: 0.9}, provider=provider, budget=budget)
    assert budget.spent_usd == pytest.approx(summary["cost_usd"]) and budget.spent_usd > 0


def test_a_failed_model_call_leaves_tier_one_and_is_counted(tmp_path):
    summary, rows, _ = window(tmp_path, {1: 0.9}, provider=ScriptedProvider(fail=True))
    assert rows[1].verdict.skipped == "model_error" and summary["model_errors"] == 1


def test_an_answer_the_gate_rejects_leaves_tier_one(tmp_path):
    provider = ScriptedProvider(lambda s, u: answer(90, "vandalism", 4, 9))
    _, rows, _ = window(tmp_path, {1: 0.9}, provider=provider)
    assert rows[1].verdict.skipped == "gate" and rows[1].verdict.risk == 0.9
    assert rows[1].verdict.gate == ["RANGE_OUTSIDE_CHANGE"]


def test_a_change_that_cannot_be_read_never_reaches_the_model(tmp_path):
    provider = ScriptedProvider()
    gone = lambda batch: {e.rev_id: Change(rev_id=e.rev_id, available=False) for e in batch}  # noqa: E731
    _, rows, _ = window(tmp_path, {1: 0.9}, provider=provider, fetch=gone)
    assert rows[1].verdict.source == "tier1" and not provider.calls


def test_a_feed_error_in_the_stream_fails_the_run(tmp_path):
    def broken():
        yield make_edit(1)
        raise FeedError("the stream changed shape")

    cascade = cascade_for(tmp_path)
    cascade.tier1 = ScoredTier1({1: 0.1})
    with pytest.raises(FeedError):
        run_window(
            cascade,
            broken(),
            tier2=None,
            fetch=lambda b: {},
            log=PredictionLog(tmp_path / "l.jsonl"),
        )
    assert not (tmp_path / "l.jsonl").exists()


def test_tier_1_does_not_wait_for_the_request_to_the_wiki(tmp_path):
    # The first live run showed this: a request that takes seconds held up the scores
    # of every edit that arrived meanwhile. The request now has a thread of its own.
    cascade = cascade_for(tmp_path)
    cascade.tier1 = ScoredTier1({1: 0.9, 2: 0.1})
    log = PredictionLog(tmp_path / "live.jsonl")
    started, release = threading.Event(), threading.Event()
    placed_while_fetching: list[bool] = []

    def slow_fetch(batch):
        started.set()
        release.wait(10)
        return {e.rev_id: Change(rev_id=e.rev_id, lines=["1 + added text"]) for e in batch}

    def stream():
        yield make_edit(1)
        started.wait(10)
        yield make_edit(2, minute=1)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (tmp_path / "live.jsonl").exists() and any(p.rev_id == 2 for p in log.read()):
                placed_while_fetching.append(not release.is_set())
                break
            time.sleep(0.02)
        release.set()

    provider = ScriptedProvider(lambda s, u: answer(90, "vandalism", 1, 1))
    summary = run_window(cascade, stream(), tier2=Tier2(provider), fetch=slow_fetch, log=log)
    assert placed_while_fetching == [True]
    assert summary["edits"] == 2 and summary["read_by_model"] == 1


def test_a_feed_error_in_the_request_to_the_wiki_fails_the_run(tmp_path):
    def broken(batch):
        raise FeedError("the revisions response changed shape")

    cascade = cascade_for(tmp_path)
    cascade.tier1 = ScoredTier1({1: 0.9})
    provider = ScriptedProvider(lambda s, u: answer(90, "vandalism", 1, 1))
    with pytest.raises(FeedError):
        run_window(
            cascade,
            iter([make_edit(1)]),
            tier2=Tier2(provider),
            fetch=broken,
            log=PredictionLog(tmp_path / "l.jsonl"),
        )
    assert not provider.calls


def test_one_request_to_the_wiki_at_a_time(tmp_path):
    active, most = [0], [0]
    lock = threading.Lock()

    def counting_fetch(batch):
        with lock:
            active[0] += 1
            most[0] = max(most[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return {e.rev_id: Change(rev_id=e.rev_id, lines=["1 + added text"]) for e in batch}

    def stream():
        for rev in range(1, 7):
            yield make_edit(rev, minute=rev)
            time.sleep(0.03)

    cascade = cascade_for(tmp_path)
    cascade.tier1 = ScoredTier1({rev: 0.9 for rev in range(1, 7)})
    provider = ScriptedProvider(lambda s, u: answer(90, "vandalism", 1, 1))
    summary = run_window(
        cascade, stream(), tier2=Tier2(provider), fetch=counting_fetch,
        log=PredictionLog(tmp_path / "l.jsonl"),
    )  # fmt: skip
    assert summary["read_by_model"] == 6 and most[0] == 1


def test_the_log_holds_no_text_of_the_change(tmp_path):
    provider = ScriptedProvider(lambda s, u: answer(90, "vandalism", 1, 1))
    window(tmp_path, {1: 0.9}, provider=provider)
    assert "added text" not in (tmp_path / "live.jsonl").read_text()


def test_seconds_into_day_and_the_run_log(tmp_path):
    assert seconds_into_day(at(0).replace(hour=1, minute=1, second=1)) == 3661
    settings = Settings(home=tmp_path)
    append_run(settings, {"edits": 3})
    append_run(settings, {"edits": 4})
    assert [r["edits"] for r in read_runs(settings)] == [3, 4]
