import pytest
from typer.testing import CliRunner

from amlagent.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AML_AGENT_DB", str(tmp_path / "cases.db"))
    monkeypatch.setenv("AML_AGENT_CASSETTES", str(tmp_path / "cassettes"))


def test_baseline_run_then_trace():
    result = runner.invoke(app, ["run", "--config", "baseline", "--alert", "AL-001"])
    assert result.exit_code == 0, result.output
    assert "AL-001" in result.output and "baseline" in result.output
    trace = runner.invoke(app, ["trace", "AL-001", "--config", "baseline"])
    assert trace.exit_code == 0 and "queue" in trace.output.lower()
    hidden = runner.invoke(app, ["trace", "AL-001", "--config", "baseline"]).output
    shown = runner.invoke(app, ["trace", "AL-001", "--config", "baseline", "--reveal"]).output
    assert "TXN_0001" in hidden and "TXN_0001" not in shown  # tokens restored only on request


def test_model_run_without_a_recording_explains_itself():
    result = runner.invoke(app, ["run", "--config", "routed", "--alert", "AL-001"])
    assert result.exit_code == 2 and "--live" in result.output


def test_eval_without_recordings_reports_the_baseline_only(tmp_path):
    out = tmp_path / "report.md"
    result = runner.invoke(app, ["eval", "--out", str(out)])
    assert result.exit_code == 0, result.output
    report = out.read_text(encoding="utf-8")
    assert "| Rules only | 28 |" in report and "| Rules only | 12 |" in report
    assert "not recorded" in report and "Scrubber:" in report


def test_leak_check_and_budget_run_clean():
    assert runner.invoke(app, ["leak-check"]).exit_code == 0
    budget = runner.invoke(app, ["budget"])
    assert budget.exit_code == 0 and "$" in budget.output


def test_trace_of_an_unknown_case_fails_politely():
    result = runner.invoke(app, ["trace", "AL-404", "--config", "baseline"])
    assert result.exit_code != 0 and "aml-agent run" in result.output


def test_nim_check_refuses_without_confirmation():
    result = runner.invoke(app, ["nim-check"])
    assert result.exit_code != 0 and "--yes" in result.output
