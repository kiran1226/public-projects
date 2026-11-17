from pathlib import Path

import pytest
from typer.testing import CliRunner

from revwatch import cli
from revwatch.cli import app
from revwatch.llm import ScriptedProvider, StepRequest, StepResponse, Usage

runner = CliRunner()

MINI = """\
- id: mini-pay
  kind: incident
  cause: payment_provider_failure
  start: 2026-07-31T10:00
  end: 2026-07-31T16:00
  slice: {country: DE, payment_method: card}
  effects: [{param: payment_success_mult, value: 0.3}]
"""


def test_eval_before_generate_says_what_to_run(tmp_path: Path) -> None:
    result = runner.invoke(app, ["eval", "--path", str(tmp_path / "nope.duckdb")])
    assert result.exit_code == 2
    assert "error: world not found" in result.output
    assert "revwatch generate" in result.output
    assert "Traceback" not in result.output


def test_broken_scenario_file_names_the_scenario(tmp_path: Path) -> None:
    scenarios = tmp_path / "bad.yaml"
    scenarios.write_text(MINI.replace("country: DE", "country: XX"))
    result = runner.invoke(
        app,
        [
            "generate",
            "--scenarios",
            str(scenarios),
            "--out",
            str(tmp_path / "w.duckdb"),
            "--seed",
            "1",
        ],
    )
    assert result.exit_code == 2
    assert "mini-pay: unknown country value 'XX'" in result.output
    assert "Traceback" not in result.output
    assert not (tmp_path / "w.duckdb").exists()


def test_missing_scenario_file_is_a_clear_error(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["generate", "--scenarios", str(tmp_path / "none.yaml"), "--seed", "1"]
    )
    assert result.exit_code == 2
    assert "error: scenario file not found" in result.output


def test_unknown_world_needs_a_seed(tmp_path: Path) -> None:
    scenarios = tmp_path / "mini.yaml"
    scenarios.write_text(MINI)
    result = runner.invoke(app, ["generate", "--world", "nowhere", "--scenarios", str(scenarios)])
    assert result.exit_code == 2
    assert "no seed is defined for world 'nowhere'" in result.output


def test_unknown_detector_is_a_clear_error(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["eval", "--path", str(tmp_path / "w.duckdb"), "--detector", "magic"]
    )
    assert result.exit_code == 2
    assert "unknown detector 'magic'" in result.output


def test_generate_then_eval_writes_the_report(tmp_path: Path) -> None:
    scenarios, world, report = (
        tmp_path / "mini.yaml",
        tmp_path / "mini.duckdb",
        tmp_path / "report.md",
    )
    scenarios.write_text(MINI)
    made = runner.invoke(
        app,
        [
            "generate",
            "--scenarios",
            str(scenarios),
            "--out",
            str(world),
            "--days",
            "70",
            "--seed",
            "3",
        ],
    )
    assert made.exit_code == 0, made.output
    assert world.exists() and world.with_suffix(".labels.json").exists()

    scored = runner.invoke(
        app, ["eval", "--path", str(world), "--detector", "seasonal", "--out", str(report)]
    )
    assert scored.exit_code == 0, scored.output
    assert "seasonal: scored hours 1344-1512 of 1680" in scored.output
    text = report.read_text()
    assert "## Detection" in text and "Why this metric" in text
    assert "| Incident recall | 1 of 1 |" in text
    assert "| mini-pay | incident |" in text


def test_eval_can_repeat_the_scenarios_on_other_seeds(tmp_path: Path) -> None:
    scenarios, world, report = (
        tmp_path / "mini.yaml",
        tmp_path / "mini.duckdb",
        tmp_path / "report.md",
    )
    scenarios.write_text(MINI)
    args = ["--scenarios", str(scenarios), "--out", str(world), "--days", "70", "--seed", "3"]
    assert runner.invoke(app, ["generate", *args]).exit_code == 0
    scored = runner.invoke(
        app,
        [
            "eval",
            "--path",
            str(world),
            "--scenarios",
            str(scenarios),
            "--detector",
            "seasonal",
            "--seeds",
            "4,5",
            "--out",
            str(report),
        ],
    )
    assert scored.exit_code == 0, scored.output
    text = report.read_text()
    assert "## Other seeds" in text
    assert "| 4 | " in text and "| 5 | " in text


def test_a_yaml_syntax_error_does_not_show_a_traceback(tmp_path: Path) -> None:
    scenarios = tmp_path / "broken.yaml"
    scenarios.write_text("- id: a\n  kind: [unclosed\n")
    result = runner.invoke(
        app,
        [
            "generate",
            "--scenarios",
            str(scenarios),
            "--out",
            str(tmp_path / "w.duckdb"),
            "--seed",
            "1",
        ],
    )
    assert result.exit_code == 2
    assert "not valid YAML" in result.output and "Traceback" not in result.output


def _mini_world(tmp_path: Path, days: int = 70, incident_on: str = "2026-07-31") -> Path:
    scenarios, world = tmp_path / "mini.yaml", tmp_path / "mini.duckdb"
    scenarios.write_text(MINI.replace("2026-07-31", incident_on))
    made = runner.invoke(
        app,
        ["generate", "--scenarios", str(scenarios), "--out", str(world), "--days", str(days)]
        + ["--seed", "3"],
    )
    assert made.exit_code == 0, made.output
    return world


def test_a_partial_run_prints_its_report_and_leaves_the_committed_one_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    committed = tmp_path / "eval-report.md"
    committed.write_text("the full report")
    monkeypatch.setattr(cli, "DEFAULT_REPORT", committed)
    world = _mini_world(tmp_path)
    result = runner.invoke(app, ["eval", "--path", str(world), "--detector", "static"])
    assert result.exit_code == 0, result.output
    assert committed.read_text() == "the full report"
    assert "# Evaluation report" in result.output and "| Incident recall |" in result.output
    assert "not written" in result.output


def test_a_world_shorter_than_the_warm_up_is_a_clear_error(tmp_path: Path) -> None:
    world = _mini_world(tmp_path, days=42, incident_on="2026-06-20")
    result = runner.invoke(app, ["eval", "--path", str(world), "--detector", "static"])
    assert result.exit_code == 2
    assert "error: the world has 42 days" in result.output and "56" in result.output
    assert "Traceback" not in result.output


def test_a_file_that_is_not_a_world_does_not_show_a_traceback(tmp_path: Path) -> None:
    world = tmp_path / "notes.duckdb"
    world.write_text("not a database")
    world.with_suffix(".labels.json").write_text("[]")
    result = runner.invoke(app, ["eval", "--path", str(world)])
    assert result.exit_code == 2
    assert "not a revwatch world" in result.output
    assert "Traceback" not in result.output


def test_other_seeds_of_a_world_file_need_its_scenario_file(tmp_path: Path) -> None:
    # Without it the seeds would be built from the dev scenarios and reported as "the same".
    world = _mini_world(tmp_path)
    result = runner.invoke(app, ["eval", "--path", str(world), "--seeds", "5"])
    assert result.exit_code == 2
    assert "--scenarios" in result.output


WITH_CHANGE = MINI + (
    '  change: {kind: provider_status, entity: psp_alpha, note: "psp_alpha: elevated error rates"}\n'
    "  mitigation: {action: failover_payment_provider, target: {provider: psp_alpha, country: DE}}\n"
)


def _queue_world(tmp_path: Path) -> Path:
    scenarios, world = tmp_path / "mini.yaml", tmp_path / "mini.duckdb"
    scenarios.write_text(WITH_CHANGE)
    made = runner.invoke(
        app,
        [
            "generate",
            "--scenarios",
            str(scenarios),
            "--out",
            str(world),
            "--days",
            "70",
            "--seed",
            "3",
        ],
    )
    assert made.exit_code == 0, made.output
    return world


def test_replay_fills_the_queue_and_stats_and_show_read_it(tmp_path: Path) -> None:
    world, queue = _queue_world(tmp_path), tmp_path / "var" / "queue.sqlite"
    args = ["replay", "--path", str(world), "--detector", "seasonal", "--store", str(queue)]
    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert "queued, 0 already decided" in first.output and "carry a proposal" in first.output

    numbers = runner.invoke(app, ["stats", "--store", str(queue)])
    assert numbers.exit_code == 0, numbers.output
    assert "by_cause:" in numbers.output and "payment_provider_failure" in numbers.output
    assert "override_rate: no decisions yet" in numbers.output

    store = cli.Store(queue)
    outage = next(r for r in store.incidents() if r.finding.cause == "payment_provider_failure")
    assert outage.proposal is not None and outage.proposal.target == {
        "provider": "psp_alpha",
        "country": "DE",
    }
    shown = runner.invoke(app, ["show", outage.incident_id, "--store", str(queue)])
    assert shown.exit_code == 0, shown.output
    assert "proposal: failover_payment_provider" in shown.output
    assert "cited values recompute" in shown.output and "c1 calendar" in shown.output

    # A reviewer's decision survives a second replay, and confirms the incident.
    store.reject(outage.incident_id, "j.meier", "test")
    again = runner.invoke(app, args)
    assert again.exit_code == 0, again.output
    assert "1 already decided and left alone" in again.output
    assert cli.Store(queue).get(outage.incident_id).status == "rejected"


def test_queue_commands_say_what_to_run_first(tmp_path: Path) -> None:
    missing = tmp_path / "none.sqlite"
    for args in (["stats", "--store", str(missing)], ["show", "INC-1", "--store", str(missing)]):
        result = runner.invoke(app, args)
        assert result.exit_code == 2 and "revwatch replay" in result.output
    assert not missing.exists()
    world = _queue_world(tmp_path)
    unknown = runner.invoke(app, ["replay", "--path", str(world), "--investigator", "oracle"])
    assert unknown.exit_code == 2 and "unknown investigator" in unknown.output


def test_the_report_has_an_investigation_section_when_the_ml_detector_ran(tmp_path: Path) -> None:
    world, report = _queue_world(tmp_path), tmp_path / "report.md"
    result = runner.invoke(
        app, ["eval", "--path", str(world), "--detector", "gbm", "--out", str(report)]
    )
    assert result.exit_code == 0, result.output
    text = report.read_text()
    assert "## Investigation" in text and "`script`, in isolation" in text
    assert (
        "| mini-pay | payment_provider_failure | payment_provider_failure: right; proposal correct |"
        in text
    )
    assert text.index("## Investigation") < text.index("## Hours not scored")
    seasonal = runner.invoke(app, ["eval", "--path", str(world), "--detector", "seasonal"])
    assert "## Investigation" not in seasonal.output


def test_one_run_builds_and_scores_both_worlds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "PROJECT", tmp_path)
    monkeypatch.setattr(cli, "DEFAULT_REPORT", tmp_path / "docs" / "eval-report.md")
    monkeypatch.setattr(cli, "CASSETTES", tmp_path / "data" / "cassettes")
    (tmp_path / "data" / "scenarios").mkdir(parents=True)
    (tmp_path / "data" / "scenarios" / "dev.yaml").write_text(MINI)
    (tmp_path / "data" / "scenarios" / "heldout.yaml").write_text(
        MINI.replace("mini-pay", "held-pay").replace("DE", "FR")
    )
    made = runner.invoke(app, ["generate", "--days", "70"])
    assert made.exit_code == 0, made.output
    assert (tmp_path / "data" / "worlds" / "dev.duckdb").exists()
    assert (tmp_path / "data" / "worlds" / "heldout.duckdb").exists()

    one = runner.invoke(app, ["eval", "--world", "heldout", "--detector", "seasonal"])
    assert one.exit_code == 0, one.output
    assert "# Held-out world" in one.output and "report not written" in one.output
    assert not cli.DEFAULT_REPORT.exists()

    monkeypatch.setattr(cli, "DETECTORS", {"seasonal": cli.DETECTORS["seasonal"]})
    both = runner.invoke(app, ["eval"])
    assert both.exit_code == 0, both.output
    text = cli.DEFAULT_REPORT.read_text()
    assert text.index("# Evaluation report") < text.index("# Held-out world")
    assert "World `dev`" in text and "World `heldout`" in text
    # The report must not claim more for the held-out world than is true of it.
    assert "Nothing was tuned on it" not in text
    assert "`CAUSE_NOT_SUPPORTED` was added afterwards" in text
    assert "| mini-pay | incident |" in text and "| held-pay | incident |" in text
    assert text.count("## Watched metrics") == 1  # said once, for both worlds


def _unknown_every_time(request: StepRequest) -> StepResponse:
    finding = {"cause": "unknown", "confidence": "low", "summary": "Nothing fits."}
    block = {"type": "tool_use", "id": "tu_1", "name": "submit_finding", "input": finding}
    return StepResponse(
        content=[block],
        stop_reason="tool_use",
        model="claude-haiku-5-5",
        usage=Usage(input_tokens=3000, output_tokens=100),
        latency_s=1.2,
    )


def test_the_agent_is_scored_from_recordings_and_left_out_without_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, report = _queue_world(tmp_path), tmp_path / "report.md"
    cassettes = tmp_path / "cassettes"
    monkeypatch.setattr(cli, "CASSETTES", cassettes)
    args = ["eval", "--path", str(world), "--detector", "gbm", "--out", str(report)]

    without = runner.invoke(app, args)
    assert without.exit_code == 0, without.output
    assert "The agent is not in this table" in report.read_text()
    assert (
        "`agent`"
        not in report.read_text()
        .split("## Hours not scored")[0]
        .split("## Investigation")[1]
        .split("Definitions")[0]
    )
    refused = runner.invoke(
        app,
        [
            "replay",
            "--path",
            str(world),
            "--investigator",
            "agent",
            "--store",
            str(tmp_path / "q.sqlite"),
        ],
    )
    assert (
        refused.exit_code == 2
        and "no recorded agent run" in refused.output
        and "--live" in refused.output
    )
    assert runner.invoke(app, [*args, "--record"]).exit_code == 2  # --record needs --live

    # A live run, with a stand-in for the API, records every step.
    monkeypatch.setattr(
        cli, "AnthropicProvider", lambda concurrency: ScriptedProvider(_unknown_every_time)
    )
    capped = runner.invoke(app, [*args, "--live", "--record", "--max-usd", "0"])
    assert capped.exit_code == 2 and "nothing more was sent" in capped.output
    assert not (cassettes / "mini.json").exists()  # the limit was reached before any call
    recorded = runner.invoke(app, [*args, "--live", "--record"])
    assert recorded.exit_code == 0, recorded.output
    assert (cassettes / "mini.json").exists()
    live_text = report.read_text()
    assert "`agent`, end to end" in live_text and "`agent`, in isolation" in live_text
    assert "The agent is not in this table" not in live_text

    # Without --live the same report comes out of the recordings alone.
    monkeypatch.setattr(
        cli, "AnthropicProvider", lambda concurrency: pytest.fail("no live call expected")
    )
    replayed = runner.invoke(app, args)
    assert replayed.exit_code == 0, replayed.output
    assert report.read_text() == live_text
    assert (
        "| mini-pay | payment_provider_failure | payment_provider_failure: right; proposal correct | unknown: wrong; no proposal |"
        in live_text
    )


def test_admin_starts_the_service_seeded_from_the_world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    world = _queue_world(tmp_path)
    started: dict[str, object] = {}
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda service, **kwargs: started.update(app=service, **kwargs)
    )
    state = tmp_path / "var" / "admin.sqlite"
    result = runner.invoke(
        app, ["admin", "--path", str(world), "--port", "9911", "--state", str(state)]
    )
    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:9911" in result.output
    assert (started["host"], started["port"]) == ("127.0.0.1", 9911)  # localhost only
    client = TestClient(started["app"])  # type: ignore[arg-type]
    assert client.get("/health").json()["entities"]["provider"] == 5
    known = client.post(
        "/payment-providers/psp_alpha/failover",
        json={"incident_id": "INC-1", "reviewer": "r", "country": "DE"},
        headers={"Idempotency-Key": "k"},
    )
    assert known.status_code == 200 and state.exists()
    missing = runner.invoke(app, ["admin", "--path", str(tmp_path / "none.duckdb")])
    assert missing.exit_code == 2 and "revwatch generate" in missing.output
