"""The recorded repeat runs, replayed through today's rules. No API calls."""

import pytest

from rfq2quote.repeats import score_all


@pytest.fixture(scope="module")
def runs():
    return {run.label: run for run in score_all()}


def by_model(runs, model, field):
    return [getattr(run, field) for label, run in sorted(runs.items()) if run.model == model]


def test_every_repeat_run_is_scored_on_all_forty_requests(runs):
    assert len(runs) == 9
    assert {run.requests for run in runs.values()} == {40}


def test_no_repeat_run_has_an_unsafe_approval_under_todays_rules(runs):
    assert {label: run.unsafe for label, run in runs.items() if run.unsafe} == {}
    assert all(run.must_review_held == run.must_review == 19 for run in runs.values())


def test_size_rule_fires_only_where_the_wrong_size_was_matched(runs):
    fired = {label: run.size_mismatch for label, run in runs.items() if run.size_mismatch}
    assert fired == {
        "claude-haiku-5-5-run1": ["rfq-037"],
        "claude-haiku-5-5-run3": ["rfq-037"],
    }


def test_haiku_moves_between_runs_and_sonnet_does_not(runs):
    assert by_model(runs, "claude-haiku-5-5", "lines_exact") == [38, 39, 39, 40, 39]
    assert by_model(runs, "claude-haiku-5-5", "clean_held") == [6, 4, 4, 3, 3]
    assert by_model(runs, "claude-sonnet-5-5", "lines_exact") == [40, 40, 40]
    assert by_model(runs, "claude-sonnet-5-5", "clean_held") == [1, 1, 1]
    assert by_model(runs, "claude-opus-5-5", "lines_exact") == [39]
