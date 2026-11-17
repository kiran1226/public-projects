"""The backtest: the time split, the model sample, the model calls and the rows it writes."""

from __future__ import annotations

import threading

import pytest

from editwatch.backtest import (
    ask_model,
    fit_combiner,
    load_tier1,
    sample,
    windows,
    write_baseline_rows,
    write_rows,
)
from editwatch.cascade import Cascade
from editwatch.config import Settings
from editwatch.llm import DEFAULT_MODEL, CallStats
from editwatch.log import PredictionLog
from editwatch.models import Change
from editwatch.store import Store
from editwatch.tier2 import Tier2, Tier2Result
from editwatch.wiki import REVERTED_TAG

from conftest import (
    CALL_COST,
    ScriptedProvider,
    answer,
    at,
    make_edit,
    make_tier1,
    training_edits,
)

DAY = 24 * 60
LINES = ["1 - The ferry runs twice a day.", "2 + The ferry runs twice a week."]


def split_settings(tmp_path) -> Settings:
    """Training on day 0 and 1, development on day 2 and 3, held out on day 4."""
    return Settings(home=tmp_path, history_start=at(0), cut=at(2 * DAY),
                    held_out_start=at(4 * DAY), test_end=at(5 * DAY))  # fmt: skip


def add_history(store: Store, edits, reverted=()) -> None:
    rows = [(e, [REVERTED_TAG] if e.rev_id in reverted else []) for e in edits]
    store.add_edits(rows, source="history", reverted_tag=REVERTED_TAG)


def make_cascade(tmp_path, **fields) -> Cascade:
    """A cascade whose slice starts at a tier-1 score of 0.5."""
    fields.setdefault("model", "scripted-model")
    return Cascade(Settings(home=tmp_path), make_tier1(), **fields)


def with_changes(store: Store, rev_ids) -> Store:
    for rev_id in rev_ids:
        store.set_change(Change(rev_id=rev_id, lines=LINES))
    return store


def answered(risk: int = 80, category: str = "vandalism", lines=(0, 0)) -> Tier2Result:
    return Tier2Result(answer(risk, category, *lines), CallStats(DEFAULT_MODEL, 3000, 60, 0.8))


# -- the time split ------------------------------------------------------------------


def test_history_is_split_at_the_cut_and_at_the_start_of_the_held_out_days(store, tmp_path):
    edits = [
        make_edit(1, minute=-1),  # before the history starts
        make_edit(2, minute=0),
        make_edit(3, minute=2 * DAY - 1),
        make_edit(4, minute=2 * DAY),  # the cut itself is a development edit
        make_edit(5, minute=4 * DAY - 1),
        make_edit(6, minute=4 * DAY),  # the first held-out moment
        make_edit(7, minute=5 * DAY - 1),
        make_edit(8, minute=5 * DAY),  # the end of the test week is outside it
    ]
    add_history(store, edits)
    split = windows(store, split_settings(tmp_path))
    assert {name: [e.rev_id for e in rows] for name, rows in split.items()} == {
        "training": [2, 3],
        "development": [4, 5],
        "held_out": [6, 7],
    }


def test_edits_by_bots_and_edits_heard_on_the_stream_are_in_no_window(store, tmp_path):
    add_history(store, [make_edit(1, minute=10), make_edit(2, minute=20, bot=True),
                        make_edit(3, minute=3 * DAY, bot=True), make_edit(4, minute=3 * DAY)])  # fmt: skip
    store.add_edits([(make_edit(5, minute=30), [])], source="stream")
    split = windows(store, split_settings(tmp_path))
    assert [e.rev_id for e in split["training"]] == [1]
    assert [e.rev_id for e in split["development"]] == [4]
    assert split["held_out"] == []


# -- the sample ----------------------------------------------------------------------


def test_a_sample_is_the_same_for_the_same_seed_and_differs_between_seeds():
    edits = [make_edit(rev, minute=rev) for rev in range(1, 201)]
    first = sample(edits, 50, seed=11)
    assert len(first) == 50 and len({e.rev_id for e in first}) == 50
    assert sample(edits, 50, seed=11) == first
    assert sample(list(reversed(edits)), 50, seed=11) == list(reversed(first))
    assert sample(edits, 50, seed=13) != first


def test_a_sample_of_a_set_no_larger_than_the_size_is_the_whole_set():
    edits = [make_edit(rev, minute=rev) for rev in range(1, 21)]
    assert sample(edits, 20, seed=11) == edits
    assert sample(edits, 500, seed=11) == edits
    assert sample(edits, 500, seed=11) is not edits
    assert sample([], 500, seed=11) == []


def test_a_sample_keeps_the_edits_in_time_order():
    edits = [make_edit(rev, minute=rev) for rev in range(1, 201)]
    drawn = sample(edits, 40, seed=13)
    assert [e.timestamp for e in drawn] == sorted(e.timestamp for e in drawn)
    assert all(e in edits for e in drawn)


# -- tier 1 for the backtest ---------------------------------------------------------


def test_tier_one_cannot_be_loaded_without_history_before_the_cut(store, tmp_path):
    add_history(store, [make_edit(1, minute=3 * DAY)])  # a development edit only
    with pytest.raises(ValueError, match="No stored history before the cut"):
        load_tier1(store, split_settings(tmp_path))
    assert not (tmp_path / "var").exists()


def test_tier_one_is_trained_on_the_training_window_once_and_then_loaded(store, tmp_path):
    edits, reverted = training_edits(120)  # fifteen hours of history from T0 on
    add_history(store, edits, {rev for rev, undone in reverted.items() if undone})
    add_history(store, [make_edit(5, minute=3 * DAY)])
    settings, said = split_settings(tmp_path), []
    tier1 = load_tier1(store, settings, said.append)
    assert tier1.trained_on == 120
    assert said == ["training tier 1 on 120 edits"]
    assert [p.name for p in (tmp_path / "var").iterdir()] == [
        f"tier1-120-{edits[0].rev_id}-{edits[-1].rev_id}.pkl"
    ]
    again = load_tier1(store, settings, said.append)
    assert len(said) == 1, "the second call loads the stored model"
    assert again.quantiles == tier1.quantiles
    assert again.score(edits[0]) == tier1.score(edits[0])


# -- asking the model ----------------------------------------------------------------


def test_the_model_is_asked_only_about_slice_edits_that_have_a_stored_change(store, tmp_path):
    edits = [make_edit(rev, minute=rev, title=f"Example article {rev}") for rev in (1, 2, 3, 4)]
    scores = {1: 0.9, 2: 0.2, 3: 0.5, 4: 0.8}
    with_changes(store, [1, 2, 3])  # revision 4 is in the slice and has no change
    provider = ScriptedProvider(lambda system, user: answer(70, "vandalism", 2, 2))
    results = ask_model(make_cascade(tmp_path), Tier2(provider), store, edits, scores)
    assert sorted(results) == [1, 3]
    assert all(r.answer == answer(70, "vandalism", 2, 2) for r in results.values())
    assert len(provider.calls) == 2
    asked_about = "\n".join(user for _, user in provider.calls)
    assert "Example article 1<" in asked_about and "Example article 3<" in asked_about
    assert "Example article 2<" not in asked_about and "Example article 4<" not in asked_about


def test_asking_stops_once_the_spend_cap_is_reached(store, tmp_path):
    edits = [make_edit(rev, minute=rev) for rev in range(1, 7)]
    with_changes(store, range(1, 7))
    provider, lines = ScriptedProvider(), []
    tier2 = Tier2(provider)
    results = ask_model(make_cascade(tmp_path), tier2, store, edits, dict.fromkeys(range(1, 7), 0.9),
                        concurrency=1, cap_usd=1.5 * CALL_COST, progress=lines.append)  # fmt: skip
    assert len(provider.calls) == 2
    assert tier2.cost_usd == pytest.approx(2 * CALL_COST)
    assert [r.answer is not None for r in results.values()] == [True, True, False, False, False, False]  # fmt: skip
    assert {r.error for r in results.values() if r.answer is None} == {"cap"}
    assert lines == [f"scripted-model: 6 of 6 edits, 4 without an answer, US${2 * CALL_COST:.3f} spent"]  # fmt: skip


def test_a_failed_call_gives_a_result_without_an_answer(store, tmp_path):
    with_changes(store, [1])
    tier2 = Tier2(ScriptedProvider(fail=True))
    results = ask_model(make_cascade(tmp_path), tier2, store, [make_edit(1)], {1: 0.9})
    assert results[1].answer is None and results[1].error == "scripted failure"


def test_the_store_is_read_on_the_callers_thread_and_the_model_is_asked_in_the_pool(tmp_path):
    class WatchedStore(Store):
        def __init__(self, path):
            super().__init__(path)
            self.threads: set[int] = set()

        def change(self, rev_id):
            self.threads.add(threading.get_ident())
            return super().change(rev_id)

    # sqlite refuses a connection that is used from a thread other than the one that made it.
    store = with_changes(WatchedStore(":memory:"), range(1, 41))
    edits = [make_edit(rev, minute=rev) for rev in range(1, 41)]
    provider = ScriptedProvider()
    results = ask_model(make_cascade(tmp_path), Tier2(provider), store, edits,
                        dict.fromkeys(range(1, 41), 0.9), concurrency=4)  # fmt: skip
    assert sorted(results) == list(range(1, 41))
    assert all(r.answer is not None and r.error is None for r in results.values())
    assert store.threads == {threading.get_ident()}
    assert provider.threads and threading.get_ident() not in provider.threads
    store.close()


# -- the combiner --------------------------------------------------------------------


def test_the_combiner_is_fitted_only_on_development_edits_the_model_answered(tmp_path):
    edits = [make_edit(rev, minute=rev) for rev in range(1, 61)]
    scores = {rev: 0.6 + (rev % 7) / 100 for rev in range(1, 61)}
    reverted = {rev: rev % 2 == 0 for rev in range(1, 51)}  # 51 to 60 have no outcome
    results = {rev: answered(90 if rev % 2 == 0 else 10) for rev in range(1, 61)}
    results[1] = Tier2Result(None, error="scripted failure")
    results[2] = Tier2Result(None, error="cap")
    del results[3], results[4]  # never asked
    combiner = fit_combiner(edits, scores, results, reverted)
    assert combiner.fitted_on == 46
    assert combiner.reverted == 23
    assert combiner.weight_model > 0


def test_the_combiner_is_not_fitted_on_too_few_answers():
    edits = [make_edit(rev, minute=rev) for rev in range(1, 61)]
    results = {rev: answered(90 if rev % 2 == 0 else 10) for rev in range(1, 30)}
    reverted = {rev: rev % 2 == 0 for rev in range(1, 61)}
    with pytest.raises(ValueError, match="at least 30 edits"):
        fit_combiner(edits, dict.fromkeys(range(1, 61), 0.6), results, reverted)


# -- the rows ------------------------------------------------------------------------


def rows_for(tmp_path, store, scores, results, **kwargs):
    """Write rows for one edit per score and read them back by revision id."""
    cascade = kwargs.pop("cascade", None) or make_cascade(tmp_path)
    log = kwargs.pop("log", None) or PredictionLog(tmp_path / "backtest.jsonl")
    config = kwargs.pop("config", "cascade")
    edits = [make_edit(rev, minute=rev) for rev in scores]
    added = write_rows(cascade, config, store, edits, scores, results, log, now=at(9 * DAY),
                       **kwargs)  # fmt: skip
    return added, {p.rev_id: p for p in log.read() if p.rev_id in scores}


def test_an_edit_outside_the_slice_gets_a_tier_one_row_even_if_the_model_answered(store, tmp_path):
    with_changes(store, [1])
    added, rows = rows_for(tmp_path, store, {1: 0.31}, {1: answered()})
    assert added == 1
    verdict = rows[1].verdict
    assert (verdict.risk, verdict.tier1, verdict.source, verdict.skipped) == (0.31, 0.31, "tier1", None)  # fmt: skip
    assert rows[1].cost_usd == 0.0 and rows[1].latency_s is None


def test_an_answered_slice_edit_gets_a_model_row_with_its_cost(store, tmp_path):
    with_changes(store, [7])
    cascade = make_cascade(tmp_path)
    _, rows = rows_for(tmp_path, store, {7: 0.8}, {7: answered(85, "vandalism", (2, 2))},
                       cascade=cascade)  # fmt: skip
    row = rows[7]
    assert (row.made_at, row.written_at) == (at(7), at(9 * DAY))
    assert (row.config, row.system_version) == ("cascade", cascade.version("cascade"))
    assert row.verdict.source == "model"
    assert row.verdict.risk == pytest.approx(cascade.combiner.risk(0.8, 85), abs=1e-5)
    assert (row.verdict.model_risk, row.verdict.category) == (85, "vandalism")
    assert (row.verdict.first_line, row.verdict.last_line) == (2, 2)
    assert row.cost_usd == pytest.approx(CALL_COST)


def test_a_slice_edit_the_model_was_not_asked_about_keeps_tier_one_and_says_no_model(
    store, tmp_path
):
    _, rows = rows_for(tmp_path, store, {1: 0.8, 2: 0.2}, {})
    assert (rows[1].verdict.risk, rows[1].verdict.source) == (0.8, "tier1")
    assert rows[1].verdict.skipped == "no_model"
    assert rows[2].verdict.skipped is None


def test_without_any_results_every_edit_gets_a_plain_tier_one_row(store, tmp_path):
    _, rows = rows_for(tmp_path, store, {1: 0.8, 2: 0.2}, None, config="tier1")
    assert [(r.config, r.verdict.source, r.verdict.skipped) for r in rows.values()] == [
        ("tier1", "tier1", None),
        ("tier1", "tier1", None),
    ]


@pytest.mark.parametrize(
    ("result", "has_change", "skipped", "cost"),
    [
        (Tier2Result(None, error="scripted failure"), True, "model_error", 0.0),
        (answered(95, "vandalism", (2, 9)), True, "gate", CALL_COST),
        (answered(95, "vandalism"), False, "model_error", CALL_COST),
    ],
    ids=["no answer", "answer the gate rejects", "no stored change"],
)
def test_a_slice_edit_without_a_usable_answer_keeps_tier_one_and_says_why(
    store, tmp_path, result, has_change, skipped, cost
):
    if has_change:
        with_changes(store, [1])
    _, rows = rows_for(tmp_path, store, {1: 0.8}, {1: result})
    assert (rows[1].verdict.risk, rows[1].verdict.source) == (0.8, "tier1")
    assert rows[1].verdict.skipped == skipped
    assert rows[1].cost_usd == pytest.approx(cost)


def test_writing_the_same_rows_again_adds_nothing(store, tmp_path):
    with_changes(store, [1])
    scores, results = {1: 0.8, 2: 0.2, 3: 0.7}, {1: answered()}
    assert rows_for(tmp_path, store, scores, results)[0] == 3
    assert rows_for(tmp_path, store, scores, results)[0] == 0
    assert len((tmp_path / "backtest.jsonl").read_text().splitlines()) == 3


def test_a_version_extra_gives_the_rows_a_system_version_of_their_own(store, tmp_path):
    cascade = make_cascade(tmp_path)
    log = PredictionLog(tmp_path / "backtest.jsonl")
    scores = {1: 0.8, 2: 0.2}
    for extra in ("", "@development", "@held-out"):
        added, _ = rows_for(tmp_path, store, scores, {}, cascade=cascade, log=log, version_extra=extra)  # fmt: skip
        assert added == 2
    rows = log.read()
    assert {p.config for p in rows} == {"cascade"}
    versions = {p.system_version for p in rows}
    assert len(versions) == 3 and cascade.version("cascade") in versions
    assert cascade.version("cascade@held-out") in versions


def test_the_backtest_log_holds_no_title_no_summary_and_no_text_of_the_change(store, tmp_path):
    with_changes(store, [1])
    rows_for(tmp_path, store, {1: 0.8}, {1: answered(85, "vandalism", (2, 2))})
    written = (tmp_path / "backtest.jsonl").read_text()
    for kept_out in ("Example article", "added a reference", "ferry"):
        assert kept_out not in written


def test_baseline_rows_are_written_only_where_the_baseline_gave_a_score(tmp_path):
    cascade = make_cascade(tmp_path)
    log = PredictionLog(tmp_path / "backtest.jsonl")
    edits = [make_edit(rev, minute=rev) for rev in (1, 2, 3, 4)]
    scores = {1: 0.8, 2: 0.2, 3: 0.4, 4: 0.6}
    baseline = {1: 0.07, 2: None, 4: 0.0}  # revision 3 was never scored
    assert write_baseline_rows(cascade, edits, scores, baseline, log, now=at(9 * DAY)) == 2
    rows = {p.rev_id: p for p in log.read()}
    assert sorted(rows) == [1, 4]
    assert {(p.config, p.system_version) for p in rows.values()} == {
        ("wikimedia", "revertrisk-language-agnostic")
    }
    assert (rows[1].verdict.risk, rows[1].verdict.tier1) == (0.07, 0.8)
    assert (rows[4].verdict.risk, rows[4].verdict.source) == (0.0, "tier1")
    assert (rows[1].made_at, rows[1].written_at) == (at(1), at(9 * DAY))
    assert write_baseline_rows(cascade, edits, scores, baseline, log, now=at(9 * DAY)) == 0
