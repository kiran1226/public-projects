"""The report, the scoreboard and the command line, on a small tracker in a temporary home."""

from __future__ import annotations

import json
from datetime import date

import pytest
import yaml
from typer.testing import CliRunner

from triagedesk.cli import app
from triagedesk.config import GroupConfig, Settings, load_settings, to_mapping
from triagedesk.models import TimelineEvent
from triagedesk.report import collect, render, share
from triagedesk.scoreboard import build, write
from triagedesk.store import Store

from conftest import REPO, make_issue, world_issues

runner = CliRunner()


def new_issues():
    return [
        make_issue(
            600,
            "Reverb bus pitch drifts",
            "### Issue description\nThe reverb bus changes pitch during playback.",
            day=101,
            labels=["topic:audio"],
        ),
        make_issue(
            601,
            "Raycast misses shape",
            "### Issue description\nThe raycast misses the collision shape.",
            day=102,
            labels=["topic:physics"],
        ),
        make_issue(
            602,
            "Mixer volume resets on stream",
            "### Issue description\nThe mixer volume resets when the stream restarts.",
            day=103,
            state="closed",
            state_reason="duplicate",
        ),
        make_issue(
            603,
            "Font ignored by theme",
            "### Issue description\nThe label font is ignored by the theme.",
            day=125,
            labels=["topic:gui", "platform:web"],
        ),
        make_issue(
            604,
            "Gravity joint explodes",
            "### Issue description\nThe joint explodes when gravity changes.",
            day=126,
        ),
    ]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home with a store, a configuration and a backtest run through the CLI."""
    settings = Settings(
        repo=REPO,
        groups=(GroupConfig("area", "topic:", 5), GroupConfig("platform", "platform:", 3)),
        history_start=date(2025, 12, 1),
        cut=date(2026, 4, 11),
        held_out_start=date(2026, 5, 1),
        test_end=date(2026, 5, 31),
        home=tmp_path,
    )
    (tmp_path / "triagedesk.yaml").write_text(
        yaml.safe_dump(to_mapping(settings)), encoding="utf-8"
    )
    store = Store(settings.db_path)
    store.add_issues(world_issues() + new_issues())
    store.set_timeline(
        602,
        [
            TimelineEvent(event="commented", refs=[6], says_duplicate=True),
            TimelineEvent(event="closed", state_reason="duplicate"),
        ],
    )
    store.close()
    monkeypatch.setenv("TRIAGEDESK_HOME", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert runner.invoke(app, ["truth", "--backtest"]).exit_code == 0
    result = runner.invoke(app, ["replay", "--configs", "rules,classifier", "--stub-embedder"])
    assert result.exit_code == 0, result.output
    return tmp_path


def test_truth_writes_outcomes_and_the_public_taxonomy(home):
    outcomes = [
        json.loads(line)
        for line in (home / "data/log/outcomes-backtest.jsonl").read_text().splitlines()
    ]
    assert [o["number"] for o in outcomes] == [600, 601, 602, 603, 604]
    assert outcomes[2]["duplicate"] and outcomes[2]["original"] == 6
    taxonomy = json.loads((home / "data/taxonomy.json").read_text())
    assert all(label["examples"] == [] for label in taxonomy["labels"])


def test_replay_writes_the_log_and_the_meta_beside_it(home):
    meta = json.loads((home / "data/log/backtest-meta.json").read_text())
    assert meta["training_issues"] == 90 and meta["stored_issues"] == 95
    assert {v["config"] for v in meta["versions"].values()} == {"rules", "classifier"}
    assert (home / "data/log/backtest.jsonl.gz").exists()


def test_a_second_replay_adds_nothing(home):
    result = runner.invoke(app, ["replay", "--configs", "rules,classifier", "--stub-embedder"])
    assert "0 predictions written, 10 already in the log" in result.output


def test_a_model_configuration_without_a_recording_falls_back_and_says_so(home):
    result = runner.invoke(app, ["replay", "--configs", "model", "--stub-embedder"])
    assert result.exit_code == 0, result.output
    data = collect(load_settings(home))
    model = next(s for s in data["development"] if s.config == "model")
    assert model.model_answers == 0 and model.reasons["model_unavailable"] == model.issues


def test_the_committed_log_holds_no_issue_text(home):
    import gzip

    log = gzip.open(home / "data/log/backtest.jsonl.gz", "rt").read()
    outcomes = (home / "data/log/outcomes-backtest.jsonl").read_text()
    for text in ("Reverb", "raycast", "explodes", "Issue description"):
        assert text not in log and text not in outcomes


def test_collect_splits_development_and_held_out_by_creation_day(home):
    data = collect(load_settings(home))
    assert data["development_truth"]["issues"] == 3
    assert data["held_out_truth"]["issues"] == 2
    assert [s.config for s in data["development"]] == ["rules", "classifier"]
    assert all(s.issues == 3 for s in data["development"])
    assert data["development_truth"]["resolved"] == 1


def test_the_report_names_what_was_scored_and_how_to_read_it(home):
    text = render(collect(load_settings(home)))
    assert text.startswith("# Evaluation report")
    for expected in (
        "## Held-out months", "## Development months", "New issue called a duplicate",
        "90 of them opened before the cut", "The live run has not started.",
        "reference, not the truth",
    ):  # fmt: skip
        assert expected in text
    assert "nan" not in text.lower()


def test_eval_writes_the_report_and_is_reproducible(home):
    first = runner.invoke(app, ["eval"])
    assert first.exit_code == 0, first.output
    report = (home / "docs/eval-report.md").read_text()
    runner.invoke(app, ["eval"])
    assert (home / "docs/eval-report.md").read_text() == report
    assert "Classifier (TF-IDF, logistic regression)" in first.output


def test_eval_can_print_without_writing(home):
    assert runner.invoke(app, ["eval", "--no-write"]).exit_code == 0
    assert not (home / "docs/eval-report.md").exists()


def test_the_scoreboard_has_every_number_the_page_shows(home):
    board = build(load_settings(home))
    assert board["repo"] == REPO and board["live"]["since"] is None
    assert board["backtest"]["development_truth"]["issues"] == 3
    row = board["backtest"]["development"][0]
    for key in ("label", "issues", "labels_right", "labels_proposed", "labels_present", "primary_right",
                "labelled", "duplicates", "flagged_right", "false_duplicates", "alone", "alone_clean",
                "cost_per_1000"):  # fmt: skip
        assert key in row
    assert board["cost"] == {
        "cap_usd_per_day": 0.5,
        "today_usd": 0.0,
        "last_30_days_usd": 0.0,
        "live_total_usd": 0.0,
    }


def test_the_page_embeds_its_data_and_loads_nothing_from_the_network(home):
    page = write(
        load_settings(home), health={"ok": True, "checked_at": "now", "checks": []}
    ).read_text()
    assert "/*SCOREBOARD_DATA*/null" not in page
    assert '"project": "issue-triage-desk"' in page
    assert "http://" not in page and "https://" not in page
    assert "<script src" not in page and "<link" not in page


def test_text_in_the_data_cannot_end_the_pages_script_block(home, monkeypatch):
    settings = load_settings(home)
    health = {
        "ok": False,
        "checked_at": "now",
        "checks": [{"name": "x", "ok": False, "detail": "</script><b>"}],
    }
    page = write(settings, health=health).read_text()
    assert page.count("</script>") == 2
    assert "<\\/script><b>" in page


def test_the_scoreboard_command_writes_both_files(home):
    result = runner.invoke(app, ["scoreboard"])
    assert result.exit_code == 0, result.output
    assert (home / "var/scoreboard/scoreboard.json").exists()
    assert (home / "var/scoreboard/index.html").exists()


def test_the_queue_says_what_to_run_when_there_is_no_live_log(home):
    result = runner.invoke(app, ["queue"])
    assert result.exit_code == 0 and "triagedesk run" in result.output


def test_apply_is_refused_for_a_repository_that_is_not_on_the_allow_list(home):
    result = runner.invoke(app, ["run", "--apply", "--no-model", "--stub-embedder"])
    assert result.exit_code == 2
    assert "not in apply_allow" in result.output
    assert not (home / "data/log/live.jsonl").exists()


def test_replay_without_history_explains_the_first_step(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIAGEDESK_HOME", str(tmp_path))
    result = runner.invoke(app, ["replay", "--stub-embedder"])
    assert result.exit_code != 0
    assert "backfill" in str(result.exception) or "backfill" in result.output


def test_share_reads_as_a_count_first():
    assert share(3, 12) == "3 of 12 (25%)"
    assert share(0, 0) == "n/a"


def test_runs_over_different_numbers_of_issues_are_not_reported_as_repeats():
    from triagedesk.evals import Counts, Score
    from triagedesk.report import spread_lines

    def run(issues: int, right: int) -> Score:
        return Score(
            config="cascade", system_version="v", issues=issues,
            labels={"area": Counts(right=right, wrong=10)},
        )  # fmt: skip

    # A run over one month and a run over three are not the same issues.
    assert spread_lines([run(300, 100), run(900, 310)], {}) == []
    lines = spread_lines([run(300, 100), run(900, 310), run(900, 320)], {})
    assert len(lines) == 1
    assert "2 runs on the same 900 issues" in lines[0] and "310 to 320" in lines[0]
