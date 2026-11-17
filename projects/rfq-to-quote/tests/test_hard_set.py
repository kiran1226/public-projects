"""The harder set: 32 requests, replayed from the recordings. No API calls."""

import json

import pytest
from typer.testing import CliRunner

from rfq2quote.cli import app
from rfq2quote.evals import ORIGINAL_SPLITS, SPLITS, load_gold, run_eval
from rfq2quote.hard import score_hard, table
from rfq2quote.intake import load_document

# Left out of the 36 that were written: the text has a defect, so "clean" is doubtful.
NOT_ADOPTED = {"hard-012", "hard-014", "hard-028", "hard-032"}
HAIKU, SONNET, OPUS = "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"
REPEATS = [f"llm-v2, {HAIKU}, repeat {n}" for n in range(1, 6)]


@pytest.fixture(scope="module")
def hard(data_dir):
    return load_gold(data_dir, ("hard",))


@pytest.fixture(scope="module")
def runs():
    return {run.label: run for run in score_hard()}


def test_the_set_is_the_32_adopted_requests(hard):
    assert SPLITS["hard"] == "Harder set"
    assert len(hard) == 32 and sum(len(g.items) for g in hard) == 96
    assert not {g.id for g in hard} & NOT_ADOPTED
    assert {g.id for g in hard} | NOT_ADOPTED == {f"hard-{n:03d}" for n in range(1, 37)}


def test_every_labelled_line_stands_word_for_word_in_its_request(hard, data_dir):
    # The label was fixed in code before the text was written around it.
    for g in hard:
        text = load_document(data_dir / g.source).text
        assert g.customer_name in text and g.customer_email in text, g.id
        for item in g.items:
            assert item.text in text, f"{g.id}: {item.text}"


def test_one_row_per_recorded_run(runs):
    assert list(runs) == [
        "baseline",
        f"llm-v1, {HAIKU}",
        f"llm-v2, {HAIKU}",
        f"llm-v2, {SONNET}",
        f"llm-v2, {OPUS}",
        *REPEATS,
    ]
    for run in runs.values():
        assert (run.requests, run.lines, run.must_review, run.clean) == (32, 96, 13, 19)


def test_rules_alone_fail_on_the_harder_set(runs):
    baseline = runs["baseline"]
    assert (baseline.lines_exact, baseline.lines_right) == (0, 16)
    assert (baseline.must_review_held, baseline.unsafe, baseline.clean_held) == (11, 3, 18)
    assert baseline.cost_per_request == 0


def test_no_model_run_has_an_unsafe_approval_or_misses_a_must_review_quote(runs):
    models = {label: run for label, run in runs.items() if label != "baseline"}
    assert len(models) == 9
    assert {label: run.unsafe for label, run in models.items() if run.unsafe} == {}
    assert all(run.must_review_held == 13 for run in models.values())
    assert all(run.size_mismatch == [] for run in models.values())


def test_the_two_prompts_differ_and_the_three_models_do_not(runs):
    def row(label):
        run = runs[label]
        return run.lines_exact, run.lines_right, run.clean_held

    assert row(f"llm-v1, {HAIKU}") == (30, 94, 12)
    assert row(f"llm-v2, {HAIKU}") == (32, 96, 3)
    assert row(f"llm-v2, {SONNET}") == (32, 96, 0)
    assert row(f"llm-v2, {OPUS}") == (32, 96, 2)


def test_repeat_runs_of_the_default_configuration(runs):
    assert [runs[label].lines_exact for label in REPEATS] == [31, 31, 32, 32, 32]
    assert [runs[label].lines_right for label in REPEATS] == [95, 95, 96, 96, 96]
    assert [runs[label].clean_held for label in REPEATS] == [3, 4, 3, 0, 2]


def test_table_gives_counts(runs):
    text = table(list(runs.values()))
    assert "| baseline | 0 of 32 | 16 of 96 | 11 of 13 | 3 | 18 of 19 | none |" in text
    assert f"| llm-v2, {SONNET} | 32 of 32 | 96 of 96 | 13 of 13 | 0 | 0 of 19 |" in text


def test_command_prints_the_table():
    result = CliRunner().invoke(app, ["hard"])
    assert result.exit_code == 0, result.output
    assert f"| llm-v2, {HAIKU}, repeat 5 | 32 of 32 | 96 of 96 | 13 of 13 | 0 | 2 of 19 |" in (
        result.output
    )


def test_recordings_hold_nothing_for_the_requests_left_out(data_dir):
    names = ("Sundvik", "Ulmenau", "Steinfurth", "Wickrath")  # the four customers left out
    files = [*(data_dir / "cassettes" / "hard").glob("*.json")]
    files += (data_dir / "repeat-runs" / "hard").glob("*.json")
    assert len(files) == 8
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not any(name in text for name in names), path.name
        assert all(entry["stats"]["model"] in path.name for entry in json.loads(text).values())


def test_eval_replays_all_three_sets_and_the_original_numbers_do_not_move(data_dir):
    report, reports = run_eval(["llm-v2"], data_dir, model=HAIKU, effort="medium")
    (llm,) = reports
    assert llm.skipped is None

    def counts(split):
        cases = llm.split(split)
        clean = [c for c in cases if c.expected_route == "auto_approve"]
        return (
            len(cases),
            sum(c.lines_exact for c in cases),
            sum(c.route == "human_review" for c in clean),
            sum(c.unsafe_auto_approval for c in cases),
        )

    assert counts("dev") == (30, 30, 1, 0)
    assert counts("holdout") == (10, 10, 1, 0)
    assert counts("hard") == (32, 32, 3, 0)
    assert llm.metrics("dev")["cost_per_rfq_usd"] == pytest.approx(0.0011, abs=5e-5)
    assert "## Harder set" in report
    # The matcher on its own is reported for the original sets as before, and apart for
    # the harder set.
    assert "| `llm-match` | Development and held-out sets | 100.0% of 101 | 0 |" in report
    assert "| `llm-match` | Harder set | 100.0% of 96 | 0 |" in report
    assert ORIGINAL_SPLITS == ("dev", "holdout")
