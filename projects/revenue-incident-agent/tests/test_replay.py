from pathlib import Path

import pytest

from revwatch.investigate.base import Case, Investigation, StopRun
from revwatch.investigate.script import ScriptInvestigator
from revwatch.investigation_evals import (
    WHY,
    cases_from_labels,
    labels_for,
    ongoing_at,
    render_investigation,
    score_investigation,
)
from revwatch.models import Incident, Slice
from revwatch.replay import handle, queue
from revwatch.scenarios import Label, Mitigation
from revwatch.store import Store
from revwatch.tools import Toolbox
from revwatch.warehouse import Warehouse
from tests.conftest import START, at


@pytest.fixture()
def world(small_world: tuple[Path, list[Label]]) -> tuple[Path, list[Label]]:
    return small_world


def test_a_case_is_built_for_every_labelled_incident(world: tuple[Path, list[Label]]) -> None:
    cases = cases_from_labels(world[1], START)
    assert set(cases) == {"bot", "pay"}  # the look-alikes are not incidents
    pay, bot = cases["pay"], cases["bot"]
    assert (pay.metric, pay.slice, pay.direction) == (
        "payment_success_rate",
        Slice.of(country="DE", payment_method="card"),
        -1,
    )
    assert pay.alert_tick == 10 * 24 + 12 and pay.incident_id == "GT-pay"
    assert (bot.metric, bot.slice, bot.direction) == (
        "sessions",
        Slice.of(country="FR", channel="affiliate"),
        1,
    )


def test_every_case_is_investigated_checked_and_kept_in_order(
    world: tuple[Path, list[Label]],
) -> None:
    cases = list(cases_from_labels(world[1], START).values())
    seen: list[str] = []
    handled = handle(world[0], cases, ScriptInvestigator(), concurrency=4, progress=seen.append)
    assert [h.case for h in handled] == cases
    assert [h.investigation.finding.cause for h in handled] == [
        "bot_traffic",
        "payment_provider_failure",
    ]
    assert all(h.assessment.reasons == () and h.assessment.mitigation is not None for h in handled)
    assert len(seen) == 2 and "investigated 2 of 2" in seen[-1]


class Broken:
    name = "broken"

    def investigate(self, case: Case, toolbox: Toolbox) -> Investigation:
        raise KeyError("observed")


class NoRecording:
    name = "agent"

    def investigate(self, case: Case, toolbox: Toolbox) -> Investigation:
        raise StopRun("no recorded response and live calls are off")


def test_an_investigation_that_breaks_is_queued_with_the_reason(
    world: tuple[Path, list[Label]],
) -> None:
    cases = list(cases_from_labels(world[1], START).values())
    handled = handle(world[0], cases, Broken())
    assert len(handled) == 2
    for item in handled:
        assert item.investigation.finding.cause == "unknown"
        assert item.assessment.codes == ("INVESTIGATION_ERROR",)
        assert "KeyError" in item.assessment.reasons[0].detail
    with pytest.raises(StopRun, match="live calls are off"):
        handle(world[0], cases, NoRecording())


def test_handled_alerts_are_written_to_the_queue(
    world: tuple[Path, list[Label]], tmp_path: Path
) -> None:
    cases = list(cases_from_labels(world[1], START).values())
    handled = handle(world[0], cases, ScriptInvestigator())
    store = Store(tmp_path / "queue.sqlite")
    assert queue(store, world[0], "gbm", "script", handled) == (2, 0)
    pay = store.get("GT-pay")
    assert pay.opened_at == at(10, 12) and pay.detector == "gbm" and pay.investigator == "script"
    assert pay.proposal == Mitigation(
        action="failover_payment_provider", target={"provider": "psp_alpha", "country": "DE"}
    )
    assert pay.evidence_ok == pay.evidence_total > 0
    with Warehouse(world[0]) as warehouse:
        assert pay.world_hash == warehouse.world_hash
    store.reject("GT-bot", "j.meier")
    assert queue(store, world[0], "gbm", "script", handled) == (1, 1)


def labelled(world: tuple[Path, list[Label]]) -> list[Label]:
    """The small world's labels, with the mitigations its scenarios do not carry."""
    targets = {
        "pay": Mitigation(
            action="failover_payment_provider", target={"provider": "psp_alpha", "country": "DE"}
        ),
        "bot": Mitigation(
            action="enable_bot_challenge", target={"country": "FR", "channel": "affiliate"}
        ),
    }
    return [lb.model_copy(update={"mitigation": targets.get(lb.scenario_id)}) for lb in world[1]]


def test_investigations_are_scored_against_the_labels(world: tuple[Path, list[Label]]) -> None:
    labels = labelled(world)
    cases = cases_from_labels(labels, START)
    handled = handle(world[0], list(cases.values()), ScriptInvestigator())
    truth = {
        case.incident_id: next(lb for lb in labels if lb.scenario_id == sid)
        for sid, case in cases.items()
    }
    report = score_investigation("script", "in isolation", handled, truth, weeks=1.0)
    assert (report.alerts, report.known, report.known_ok, report.unknown) == (2, 2, 2, 0)
    assert (report.with_mitigation, report.proposals_ok, report.unsafe) == (2, 2, 0)
    assert report.evidence_ok == report.evidence_total > 0
    assert report.cost_usd == 0.0 and report.tool_calls > 3 and report.errors == 0
    assert [(r.scenario_id, r.found, r.cause_ok, r.proposal) for r in report.rows] == [
        ("bot", "bot_traffic", True, "correct"),
        ("pay", "payment_provider_failure", True, "correct"),
    ]


def test_a_proposal_on_no_incident_or_with_the_wrong_target_is_unsafe(
    world: tuple[Path, list[Label]],
) -> None:
    labels = labelled(world)
    cases = cases_from_labels(labels, START)
    handled = handle(world[0], list(cases.values()), ScriptInvestigator())
    # The same findings, scored as if the bot alert were on nothing and the outage's
    # right answer were another provider.
    pay = next(lb for lb in labels if lb.scenario_id == "pay")
    other = pay.model_copy(
        update={
            "mitigation": pay.mitigation.model_copy(
                update={"target": {"provider": "psp_beta", "country": "DE"}}
            )
        }
    )  # type: ignore[union-attr]
    report = score_investigation(
        "script", "end to end", handled, {"GT-bot": None, "GT-pay": other}, weeks=2.0
    )
    assert report.unsafe == 2 and report.proposals_ok == 0
    assert [r.proposal for r in report.rows] == ["unsafe", "unsafe"]
    assert report.rows[0].cause_ok is None and report.false_pages == 0  # bots are ticketed


def test_alerts_are_matched_to_the_incident_they_are_on(world: tuple[Path, list[Label]]) -> None:
    on_pay = Incident(
        "INC-a", 10 * 24 + 11, "payment_success_rate", Slice.of(country="DE"), -1, 10 * 24 + 11
    )
    elsewhere = Incident("INC-b", 12 * 24, "sessions", Slice.of(country="AT"), -1, 12 * 24)
    on_promo = Incident("INC-c", 16 * 24 + 5, "sessions", Slice(), 1, 16 * 24 + 5)
    truth = labels_for([on_pay, elsewhere, on_promo], world[1], START)
    assert truth["INC-a"] is not None and truth["INC-a"].scenario_id == "pay"
    assert truth["INC-b"] is None and truth["INC-c"] is None  # a promotion is not an incident


def test_the_report_states_why_each_metric_was_chosen(world: tuple[Path, list[Label]]) -> None:
    labels = labelled(world)
    cases = cases_from_labels(labels, START)
    handled = handle(world[0], list(cases.values()), ScriptInvestigator())
    truth = {
        case.incident_id: next(lb for lb in labels if lb.scenario_id == sid)
        for sid, case in cases.items()
    }
    isolated = score_investigation("script", "in isolation", handled, truth, weeks=1.0)
    end_to_end = score_investigation("script", "end to end", handled, truth, weeks=1.0)
    text = "\n".join(render_investigation([end_to_end, isolated]))
    assert "| Metric | `script`, end to end | `script`, in isolation | Why this metric |" in text
    assert "| Root-cause accuracy | 2 of 2 | 2 of 2 |" in text
    assert "| Correct declines | n/a | n/a |" in text
    assert "| Unsafe mitigation proposals | 0 | 0 |" in text
    assert "| False pages per week | 0.0 | n/a |" in text
    assert "| Cost per alert | $0.0000 | $0.0000 |" in text
    for _, why in WHY.values():
        assert why in text
    assert (
        "| pay | payment_provider_failure | payment_provider_failure: right; proposal correct |"
        in text
    )
    assert render_investigation([]) == []


def test_the_right_action_for_an_incident_going_on_is_not_unsafe_on_a_neighbouring_alert(
    world: tuple[Path, list[Label]],
) -> None:
    labels = labelled(world)
    cases = cases_from_labels(labels, START)
    handled = handle(world[0], [cases["pay"]], ScriptInvestigator())
    pay = next(lb for lb in labels if lb.scenario_id == "pay")
    # Scored as an alert that is on no incident, raised while the card outage was going on.
    report = score_investigation(
        "script", "end to end", handled, {"GT-pay": None}, 1.0, {"GT-pay": [pay]}
    )
    assert (report.unsafe, [r.proposal for r in report.rows]) == (0, ["redundant"])
    alone = score_investigation(
        "script", "end to end", handled, {"GT-pay": None}, 1.0, {"GT-pay": []}
    )
    assert alone.unsafe == 1


def test_alerts_know_which_incidents_were_going_on(world: tuple[Path, list[Label]]) -> None:
    during = Incident("INC-a", 10 * 24 + 12, "sessions", Slice.of(country="AT"), 1, 10 * 24 + 12)
    after = Incident("INC-b", 10 * 24 + 20, "sessions", Slice.of(country="AT"), 1, 10 * 24 + 20)
    found = ongoing_at([during, after], world[1], START)
    assert [lb.scenario_id for lb in found["INC-a"]] == ["pay"] and found["INC-b"] == []
