"""The report, the scoreboard and the command line, on a small store in a temporary home."""

from __future__ import annotations

import gzip
import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from editwatch.cli import app
from editwatch.config import Settings, load_settings
from editwatch.evals import Score
from editwatch.report import collect, load_outcomes, name_of, render, share
from editwatch.scoreboard import build, write
from editwatch.store import Store

from conftest import training_edits

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]


def spread(settings: Settings, n: int = 600):
    """Learnable edits with outcomes, spread evenly from the start of history to the end of the test week."""
    edits, reverted = training_edits(n)
    span = settings.test_end - settings.history_start - timedelta(hours=1)
    rows = []
    for i, edit in enumerate(edits):
        moved = edit.model_copy(update={"timestamp": settings.history_start + span * (i / n)})
        rows.append((moved, ["mw-reverted"] if reverted[edit.rev_id] else []))
    return rows


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home with a store of history and the committed fixture, and a backtest without a model."""
    settings = Settings(home=tmp_path)
    store = Store(settings.db_path)
    store.add_edits(spread(settings), source="history", reverted_tag="mw-reverted")
    store.close()
    shutil.copytree(ROOT / "data" / "fixture", tmp_path / "data" / "fixture")
    shutil.copytree(ROOT / "data" / "cassettes", tmp_path / "data" / "cassettes")
    monkeypatch.setenv("EDITWATCH_HOME", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = runner.invoke(app, ["train"])
    assert result.exit_code == 0, result.output
    (tmp_path / "data" / "samples.json").write_text(json.dumps({"development": [], "held_out": []}))
    result = runner.invoke(app, ["backtest"])
    assert result.exit_code == 0, result.output
    return tmp_path


def sample_all(home: Path) -> None:
    """Make the model samples the whole test week, so the sample tables have rows."""
    outcomes = load_outcomes(load_settings(home))
    settings = load_settings(home)
    store = Store(settings.db_path)
    dev = [e.rev_id for e in store.edits(settings.cut, settings.held_out_start)]
    held = [e.rev_id for e in store.edits(settings.held_out_start, settings.test_end)]
    assert set(dev + held) <= set(outcomes)
    (home / "data" / "samples.json").write_text(json.dumps({"development": dev, "held_out": held}))


def test_train_writes_the_summary_with_each_window(home):
    summary = json.loads((home / "data" / "tier1.json").read_text())
    assert set(summary["windows"]) == {"training", "development", "held_out"}
    assert summary["trained_on"] == summary["windows"]["training"]["edits"]
    assert sum(w["edits"] for w in summary["windows"].values()) == 600


def test_backtest_writes_tier_one_rows_and_the_outcomes_beside_them(home):
    outcomes = load_outcomes(load_settings(home))
    with gzip.open(home / "data/log/backtest.jsonl.gz", "rt") as handle:
        rows = [json.loads(line) for line in handle]
    assert {r["cfg"] for r in rows} == {"tier1"}
    assert {r["r"] for r in rows} == set(outcomes)
    assert any(outcomes.values()) and not all(outcomes.values())


def test_a_second_backtest_adds_nothing_and_the_outcome_file_is_identical(home):
    before = (home / "data/log/outcomes-backtest.json.gz").read_bytes()
    result = runner.invoke(app, ["backtest"])
    assert "tier1: 0 rows" in result.output
    assert (home / "data/log/outcomes-backtest.json.gz").read_bytes() == before


def test_the_committed_files_hold_no_title_or_summary(home):
    with gzip.open(home / "data/log/backtest.jsonl.gz", "rt") as handle:
        log = handle.read()
    assert "Example article" not in log and "added a reference" not in log
    assert "Example article" not in (home / "data/tier1.json").read_text()


def test_the_backtest_refuses_an_unknown_part(home):
    assert runner.invoke(app, ["backtest", "--part", "everything"]).exit_code == 2


def test_a_model_run_on_the_held_out_days_needs_fitted_weights_first(home):
    sample_all(home)
    result = runner.invoke(app, ["backtest", "--models", "haiku", "--part", "held_out"])
    assert result.exit_code == 2 and "development days first" in result.output


def test_collect_scores_tier_one_beside_arrival_order(home):
    sample_all(home)
    data = collect(load_settings(home))
    for part in ("development", "held_out"):
        configs = [s.config for s in data[part]]
        assert configs == ["arrival", "tier1"]
        tier1 = data[part][1]
        assert tier1.edits == data[f"{part}_n"] and tier1.reverted == data[f"{part}_reverted"]
        assert tier1.hits[0.10] >= data[part][0].hits[0.10], (
            "tier 1 beats the clock on learnable data"
        )


def test_the_report_names_what_was_scored_and_how_to_read_it(home):
    sample_all(home)
    text = render(collect(load_settings(home)))
    assert text.startswith("# Evaluation report")
    for expected in ("## Held-out days, the model sample", "## Development days, the model sample",
                     "Reverted, in the bottom half", "Arrival order (what reviewers have today)",
                     "Tier 1 alone", "The live run has not started.", "is not \"vandalism\""):  # fmt: skip
        assert expected in text
    assert "nan" not in text.lower()


def test_eval_writes_the_report_and_is_reproducible(home):
    sample_all(home)
    assert runner.invoke(app, ["eval"]).exit_code == 0
    first = (home / "docs/eval-report.md").read_text()
    runner.invoke(app, ["eval"])
    assert (home / "docs/eval-report.md").read_text() == first


def test_eval_can_print_without_writing(home):
    assert runner.invoke(app, ["eval", "--no-write"]).exit_code == 0
    assert not (home / "docs/eval-report.md").exists()


def test_a_cascade_is_named_by_its_model_and_the_share_it_reads():
    meta = {"versions": {"v1": {"run": 1, "share": 0.2}, "v2": {"run": 3, "share": 0.1},
                         "v3": {"run": 1, "share": 1.0}}}  # fmt: skip
    assert name_of(Score("cascade-sonnet", "v1"), meta) == "Cascade, Sonnet reads the top 20%"
    assert name_of(Score("cascade-opus", "v2"), meta) == "Cascade, Opus reads the top 10%, run 3"
    assert name_of(Score("every-haiku", "v3"), meta) == "Haiku reads every edit"
    assert name_of(Score("tier1", "x"), meta) == "Tier 1 alone (event fields, no model)"


def test_share_reads_as_a_count_first():
    assert share(3, 12) == "3 of 12 (25%)" and share(0, 0) == "n/a"
    assert share(1200, 2400) == "1,200 of 2,400 (50%)"


def test_the_scoreboard_has_every_number_the_page_shows(home):
    sample_all(home)
    board = build(load_settings(home))
    assert board["live"]["since"] is None and board["live"]["scores"] == []
    row = next(r for r in board["backtest"]["held_out"] if r["config"] == "tier1")
    for key in ("label", "edits", "reverted", "top2", "top5", "top10", "bottom_half", "model_read",
                "cost_per_1000"):  # fmt: skip
        assert key in row
    assert board["cost"]["cap_usd_per_day"] == 1.0
    assert board["live"]["last_run"] is None


def test_the_scoreboard_shows_the_latency_of_the_last_run_on_its_own(home):
    from editwatch.live import append_run

    sample_all(home)
    settings = load_settings(home)
    append_run(settings, {"at": "2026-11-17T22:08:19+00:00", "model_p95_s": 17.9,
                          "model_p50_s": 8.9, "tier1_p95_s": 5.6})  # fmt: skip
    append_run(settings, {"at": "2026-11-17T22:24:00+00:00", "model_p95_s": 7.0,
                          "model_p50_s": 4.4, "tier1_p95_s": 0.8})  # fmt: skip
    board = build(settings)
    assert board["live"]["runs"] == 2
    assert board["live"]["last_run"] == {"model_p50_s": 4.4, "model_p95_s": 7.0, "tier1_p95_s": 0.8}


def test_the_page_embeds_its_data_and_text_cannot_end_its_script_block(home):
    health = {"ok": False, "checked_at": "2026-11-17T20:00:00+00:00",
              "checks": [{"name": "x", "ok": False, "detail": "</script><b>"}]}  # fmt: skip
    page = write(load_settings(home), health=health).read_text()
    assert "/*SCOREBOARD_DATA*/null" not in page
    assert page.count("</script>") == 2 and "<\\/script><b>" in page
    assert "https://" not in page and "http://" not in page


def test_the_scoreboard_command_writes_both_files(home):
    assert runner.invoke(app, ["scoreboard"]).exit_code == 0
    assert (home / "var/scoreboard/scoreboard.json").exists()
    assert (home / "var/scoreboard/index.html").exists()


def test_the_load_test_runs_on_the_committed_slice_and_writes_its_table(home):
    result = runner.invoke(app, ["loadtest", "--speeds", "1,50"])
    assert result.exit_code == 0, result.output
    load = json.loads((home / "data/loadtest.json").read_text())
    assert [r["speed"] for r in load["results"]] == [1.0, 50.0]
    assert load["results"][1]["edits_per_second"] > load["results"][0]["edits_per_second"]
    assert load["edits"] > 100 and "sustained" in load


def test_the_load_test_with_every_edit_read_writes_its_own_file(home):
    plain = home / "data/loadtest.json"
    before = plain.read_text() if plain.exists() else None
    result = runner.invoke(app, ["loadtest", "--speeds", "1", "--every"])
    assert result.exit_code == 0, result.output
    load = json.loads((home / "data/loadtest-every.json").read_text())
    row = load["results"][0]
    assert row["read_by_model"] + row["skipped_queue_full"] == load["edits"]
    assert (plain.read_text() if plain.exists() else None) == before


def test_the_load_test_refuses_scores_from_another_recording(home):
    recordings = home / "var" / "recordings"
    recordings.mkdir(parents=True, exist_ok=True)
    (recordings / "other.jsonl").write_text("")
    assert runner.invoke(app, ["loadtest", "--scores-from", "other"]).exit_code == 2


def test_replay_runs_the_live_loop_on_the_committed_slice_without_a_network(home):
    result = runner.invoke(app, ["replay", "--speed", "500"])
    assert result.exit_code == 0, result.output
    assert "edits replayed at 500x" in result.output
    # The wait is counted from the replay's delivery, not from the recorded event time.
    placed = float(result.output.split("placed each within ")[1].split(" seconds")[0])
    assert placed < 5.0
    assert not (home / "data/log/live.jsonl").exists(), "a replay never writes to the live log"


def test_the_demo_runs_from_the_recording_without_a_key(home):
    result = runner.invoke(app, ["demo", "--top", "3"])
    assert result.exit_code == 0, result.output
    assert "Looks like" in result.output and "outcome:" in result.output
    assert "read by the model 16" in result.output


def test_queue_and_truth_say_so_when_there_is_no_live_run(home):
    assert "editwatch run" in runner.invoke(app, ["queue"]).output
    result = runner.invoke(app, ["truth"])
    assert result.exit_code == 0 and "No live edit" in result.output


def test_train_without_history_explains_the_first_step(tmp_path, monkeypatch):
    monkeypatch.setenv("EDITWATCH_HOME", str(tmp_path))
    result = runner.invoke(app, ["train"])
    assert result.exit_code == 1 and "backfill" in result.output
