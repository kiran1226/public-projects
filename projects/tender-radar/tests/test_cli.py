"""The commands a reader can run after cloning: no key, no feed, no database."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from tenderradar.cli import app

runner = CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point the logs, the scoreboard and the database at an empty folder."""
    monkeypatch.setenv("TENDERRADAR_HOME", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return tmp_path


def test_demo_prints_a_real_morning_list_from_recordings(home):
    result = runner.invoke(app, ["demo", "--stub-embedder"])
    assert result.exit_code == 0, result.output
    assert "Tenders first published 2026-05-12: 191" in result.output
    assert "Passed the rules: 74. Listed: 15." in result.output
    assert "model answers: 10 (10 from the recording, US$0.0000 spent now)" in result.output
    assert "without a recording: 0" in result.output


def test_eval_on_empty_logs_writes_a_report_that_says_so(home):
    out = home / "report.md"
    result = runner.invoke(app, ["eval", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "No backtest has been replayed yet." in out.read_text(encoding="utf-8")


def test_scoreboard_writes_json_and_a_page(home):
    result = runner.invoke(app, ["scoreboard"])
    assert result.exit_code == 0, result.output
    board = json.loads((home / "var" / "scoreboard" / "scoreboard.json").read_text())
    assert board["project"] == "tender-radar" and board["live"]["lists"] == 0
    assert (home / "var" / "scoreboard" / "index.html").exists()


def test_health_fails_when_no_run_has_recorded_it(home):
    result = runner.invoke(app, ["health"])
    assert result.exit_code == 1
    assert "no run has recorded" in result.output


def test_health_reports_the_last_checks_and_fails_on_a_failed_one(home):
    folder = home / "var" / "scoreboard"
    folder.mkdir(parents=True)
    checks = [{"name": "feed_answers", "ok": True, "detail": "fine"}]
    (folder / "health.json").write_text(
        json.dumps({"ok": True, "checked_at": "2026-11-17T06:00:00+00:00", "checks": checks})
    )
    assert runner.invoke(app, ["health"]).exit_code == 0
    checks.append({"name": "feed_is_fresh", "ok": False, "detail": "9 days old"})
    (folder / "health.json").write_text(
        json.dumps({"ok": False, "checked_at": "2026-11-17T06:00:00+00:00", "checks": checks})
    )
    result = runner.invoke(app, ["health"])
    assert result.exit_code == 1 and "[FAIL] feed_is_fresh: 9 days old" in result.output


def test_today_and_truth_say_so_before_the_first_live_run(home):
    assert "no list has been written" in runner.invoke(app, ["today"]).output
    assert "has not listed any tender" in runner.invoke(app, ["truth"]).output
