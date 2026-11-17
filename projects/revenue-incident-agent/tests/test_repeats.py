import json
import re
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from revwatch import cli
from revwatch.cli import app
from revwatch.findings import Citation, Finding
from revwatch.guardrails import Assessment, Held
from revwatch.investigate.base import Case, Investigation
from revwatch.investigation_evals import score_investigation, wrong_proposal
from revwatch.llm import ScriptedProvider, StepRequest, StepResponse
from revwatch.models import Slice
from revwatch.repeats import PUBLISHED, Recording, Replayed, recordings, summarise, table
from revwatch.replay import Handled
from revwatch.scenarios import Label, Mitigation
from revwatch.verify import Check
from tests.conftest import at

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

FAILOVER = Mitigation(
    action="failover_payment_provider", target={"provider": "psp_alpha", "country": "DE"}
)
ELSEWHERE = Mitigation(
    action="failover_payment_provider", target={"provider": "psp_beta", "country": "FR"}
)
PAY = Label(
    scenario_id="pay",
    kind="incident",
    cause="payment_provider_failure",
    start=at(10, 10),
    end=at(10, 16),
    slice={"country": "DE", "payment_method": "card"},
    labelled=True,
    change_id=None,
    mitigation=FAILOVER,
    moves={"payment_success_rate": -1},
)


def alert(
    incident_id: str,
    proposed: Mitigation | None,
    held: tuple[str, ...] = (),
    cause: str = "payment_provider_failure",
    cited: tuple[bool, ...] = (),
) -> Handled:
    """An alert after investigation and checks, built by hand: only what scoring reads."""
    finding = Finding(cause=cause, confidence="high", mitigation=proposed, summary="s")
    number = Citation(call_id="c1", row="DE", column="window", value=0.3)
    assessment = Assessment(
        reasons=tuple(Held(code, "") for code in held),
        mitigation=None if held else proposed,
        severity="ticket",
        impact_eur_per_hour=0.0,
        owner="payments",
        checks=tuple(Check(number, ok) for ok in cited),
    )
    case = Case(incident_id, "payment_success_rate", Slice(), -1, 250)
    return Handled(case, Investigation(finding), assessment)


def test_recordings_are_the_published_run_and_one_folder_per_repeat(tmp_path: Path) -> None:
    published, repeats = tmp_path / "cassettes", tmp_path / "repeat-runs"
    for name in ("claude-sonnet-5-5-run1", "claude-haiku-5-5-run2", "claude-haiku-5-5-run1"):
        (repeats / name).mkdir(parents=True)
    found = recordings(published, repeats)
    assert [r.label for r in found] == [
        PUBLISHED,
        "claude-haiku-5-5-run1",
        "claude-haiku-5-5-run2",
        "claude-sonnet-5-5-run1",
    ]
    assert [r.model for r in found] == ["claude-haiku-5-5"] * 3 + ["claude-sonnet-5-5"]
    assert found[0].path("dev") == published / "dev.json"
    assert found[3].path("heldout") == repeats / "claude-sonnet-5-5-run1" / "heldout.json"
    # With no repeat runs there is still the published one.
    assert [r.label for r in recordings(published, tmp_path / "none")] == [PUBLISHED]


def test_a_folder_that_is_not_named_like_a_run_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "repeat-runs" / "second-try").mkdir(parents=True)
    with pytest.raises(ValueError, match="second-try: expected <model>-run<n>"):
        recordings(tmp_path / "cassettes", tmp_path / "repeat-runs")


def test_a_withheld_proposal_is_wrong_by_the_rule_that_makes_a_passed_one_unsafe() -> None:
    assert not wrong_proposal(FAILOVER, PAY) and wrong_proposal(ELSEWHERE, PAY)
    # On an alert that is on no incident, the right action for one going on is not wrong.
    assert wrong_proposal(FAILOVER, None) and not wrong_proposal(FAILOVER, None, [PAY])
    assert wrong_proposal(ELSEWHERE, None, [PAY])
    # The same rule as the one that counts a proposal that passed as unsafe.
    handled = [alert("INC-a", FAILOVER), alert("INC-b", ELSEWHERE), alert("INC-c", FAILOVER)]
    truth = {"INC-a": PAY, "INC-b": PAY, "INC-c": None}
    ongoing = {"INC-c": [PAY]}
    report = score_investigation("agent", "end to end", handled, truth, 1.0, ongoing)
    assert [row.proposal == "unsafe" for row in report.rows] == [
        wrong_proposal(FAILOVER, PAY),
        wrong_proposal(ELSEWHERE, PAY),
        wrong_proposal(FAILOVER, None, ongoing["INC-c"]),
    ]


def test_a_run_is_summarised_as_counts() -> None:
    end_to_end = Replayed(
        handled=[
            alert("INC-right", FAILOVER, cited=(True, True)),
            alert("INC-unsafe", ELSEWHERE, cited=(True,)),  # on no incident, and it passed
            alert("INC-stopped", ELSEWHERE, held=("EXPECTED_EVENT", "CAUSE_NOT_SUPPORTED")),
            alert("INC-only-support", ELSEWHERE, held=("CAUSE_NOT_SUPPORTED",)),
            alert("INC-right-held", FAILOVER, held=("EVIDENCE_NOT_VERIFIED",), cited=(False,)),
            alert("INC-quiet", None, cause="unknown"),
        ],
        truth={
            "INC-right": PAY,
            "INC-unsafe": None,
            "INC-stopped": None,
            "INC-only-support": None,
            "INC-right-held": None,
            "INC-quiet": None,
        },
        ongoing={"INC-right-held": [PAY]},
    )
    isolated = Replayed(
        handled=[alert("GT-pay", FAILOVER, held=("CAUSE_NOT_SUPPORTED", "LOW_CONFIDENCE"))],
        truth={"GT-pay": PAY, "GT-lost": PAY},
        ongoing={},
        unreplayable=("GT-lost",),  # its recording no longer answers: named, never counted
    )
    run = summarise(
        Recording("claude-haiku-5-5-run1", "claude-haiku-5-5", Path("x")),
        [end_to_end],
        [isolated],
    )
    assert (run.label, run.model) == ("claude-haiku-5-5-run1", "claude-haiku-5-5")
    assert (run.right_cause, run.known) == (1, 1)  # in isolation: the cause was named
    assert (run.approvable, run.with_mitigation) == (0, 1)  # but its proposal was held
    assert (run.alerts, run.unsafe, run.stopped) == (6, 1, 2)
    assert run.stopped_by_support == ("INC-only-support",)  # would have passed without it
    assert run.right_held_by_support == ("GT-pay",)  # counted in both views
    assert (run.evidence_ok, run.evidence_total) == (3, 4)
    assert run.unreplayable == ("GT-lost",)

    text = table([run])
    assert text.splitlines()[0].startswith("| Run | Right cause |")
    assert text.splitlines()[0].endswith("| Investigations that no longer replay |")
    assert "| claude-haiku-5-5-run1 | 1 of 1 | 0 of 1 | 1 | 2 | 1 | 1 | 3 of 4 | 1 |" in text


def test_the_committed_repeat_recordings_are_complete_and_carry_nothing_private() -> None:
    found = recordings(cli.CASSETTES, cli.REPEATS)
    assert [r.label for r in found] == [
        PUBLISHED,
        "claude-haiku-5-5-run1",
        "claude-haiku-5-5-run2",
        "claude-sonnet-5-5-run1",
    ]
    private = re.compile(r"sk-ant|api[_-]?key|authorization|/Users/|/home/", re.IGNORECASE)
    for recording in found:
        for world in cli.SEEDS:
            text = recording.path(world).read_text(encoding="utf-8")
            entries = json.loads(text)
            assert entries and private.search(text) is None, recording.path(world)
            # No failed call is left in a recording, and one run is one model.
            assert all("response" in entry for entry in entries.values())
            assert {entry["model"] for entry in entries.values()} == {recording.model}


def test_repeats_before_generate_says_what_to_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "PROJECT", tmp_path)
    result = runner.invoke(app, ["repeats"])
    assert result.exit_code == 2 and "revwatch generate" in result.output


def _unknown_every_time(request: StepRequest) -> StepResponse:
    finding = {"cause": "unknown", "confidence": "low", "summary": "Nothing fits."}
    block = {"type": "tool_use", "id": "tu_1", "name": "submit_finding", "input": finding}
    return StepResponse(content=[block], stop_reason="tool_use", model="claude-haiku-5-5")


def test_repeats_replays_every_recording_without_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cassettes, repeat_runs = tmp_path / "data" / "cassettes", tmp_path / "data" / "repeat-runs"
    monkeypatch.setattr(cli, "PROJECT", tmp_path)
    monkeypatch.setattr(cli, "DEFAULT_REPORT", tmp_path / "docs" / "eval-report.md")
    monkeypatch.setattr(cli, "CASSETTES", cassettes)
    monkeypatch.setattr(cli, "REPEATS", repeat_runs)
    # The seasonal detector stands in for the forecaster: the alerts only have to exist.
    monkeypatch.setattr(cli, "DETECTORS", {"gbm": cli.DETECTORS["seasonal"]})
    (tmp_path / "data" / "scenarios").mkdir(parents=True)
    (tmp_path / "data" / "scenarios" / "dev.yaml").write_text(MINI)
    (tmp_path / "data" / "scenarios" / "heldout.yaml").write_text(MINI.replace("DE", "FR"))
    assert runner.invoke(app, ["generate", "--days", "70"]).exit_code == 0
    monkeypatch.setattr(
        cli, "AnthropicProvider", lambda concurrency: ScriptedProvider(_unknown_every_time)
    )
    recorded = runner.invoke(app, ["eval", "--live", "--record"])
    assert recorded.exit_code == 0, recorded.output
    shutil.copytree(cassettes, repeat_runs / "claude-haiku-5-5-run1")

    monkeypatch.setattr(
        cli, "AnthropicProvider", lambda concurrency: pytest.fail("no live call expected")
    )
    result = runner.invoke(app, ["repeats"])
    assert result.exit_code == 0, result.output
    rows = [line for line in result.output.splitlines() if line.startswith("|")]
    assert len(rows) == 4  # header, rule, the published run, the repeat
    # A stand-in model that always says `unknown` names no cause and proposes nothing.
    assert rows[2].startswith("| published | 0 of 2 | n/a | 0 | 0 | 0 | 0 |")
    assert rows[3].startswith("| claude-haiku-5-5-run1 | 0 of 2 | n/a | 0 | 0 | 0 | 0 |")

    # An investigation whose recording no longer answers is named and left out of the
    # counts; the other investigations of that run are still replayed.
    path = repeat_runs / "claude-haiku-5-5-run1" / "dev.json"
    entries = json.loads(path.read_text())
    lost = sorted(entry["tag"] for entry in entries.values() if entry["tag"].startswith("GT-"))[0]
    kept = {key: entry for key, entry in entries.items() if entry["tag"] != lost}
    path.write_text(json.dumps(kept))
    partial = runner.invoke(app, ["repeats"])
    assert partial.exit_code == 0, partial.output
    rows = [line for line in partial.output.splitlines() if line.startswith("|")]
    assert rows[2].endswith("| 0 |") and rows[3].endswith("| 1 |")
    assert rows[3].startswith("| claude-haiku-5-5-run1 | 0 of 1 |")
    assert f"claude-haiku-5-5-run1, dev: {lost}" in partial.output
    path.write_text(json.dumps(entries))

    # A run with a world missing from its recording is named, not skipped.
    (repeat_runs / "claude-haiku-5-5-run1" / "heldout.json").unlink()
    missing = runner.invoke(app, ["repeats"])
    assert missing.exit_code == 2 and "claude-haiku-5-5-run1" in missing.output
