from datetime import datetime
from pathlib import Path

import pytest

from revwatch.findings import Citation, Finding
from revwatch.guardrails import REASONS, assess, impact_per_hour, owner
from revwatch.investigate.base import Case, Investigation
from revwatch.investigate.script import ScriptInvestigator
from revwatch.models import Slice
from revwatch.policy import ReviewPolicy
from revwatch.scenarios import Label, Mitigation
from revwatch.tools import Toolbox, ToolResult
from revwatch.verify import verify
from revwatch.warehouse import Warehouse
from tests.conftest import at

DE_CARD = Slice.of(country="DE", payment_method="card")
FAILOVER = Mitigation(
    action="failover_payment_provider", target={"provider": "psp_alpha", "country": "DE"}
)


@pytest.fixture()
def wh(small_world: tuple[Path, list[Label]]) -> Warehouse:
    return Warehouse(small_world[0])


def outage(wh: Warehouse) -> tuple[Case, Investigation, Toolbox]:
    case = Case("INC-pay", "payment_success_rate", DE_CARD, -1, wh.tick(at(10, 11)))
    box = Toolbox(wh, case.now)
    return case, ScriptInvestigator().investigate(case, box), Toolbox(wh, case.now)


def codes(case: Case, investigation: Investigation, box: Toolbox) -> tuple[str, ...]:
    return assess(case, investigation, box).codes


def test_a_sound_finding_passes_and_keeps_its_proposal(wh: Warehouse) -> None:
    case, investigation, box = outage(wh)
    result = assess(case, investigation, box)
    assert result.reasons == () and result.mitigation == FAILOVER
    assert result.owner == "payments" and all(check.ok for check in result.checks)
    assert box.trace == []  # checking a finding makes no recorded tool calls of its own


def test_a_cited_value_that_does_not_recompute_holds_the_finding(wh: Warehouse) -> None:
    case, investigation, box = outage(wh)
    real = investigation.finding.evidence[0]
    invented = real.model_copy(update={"value": real.value * 1.05})
    within = real.model_copy(update={"value": real.value * 1.005})
    investigation.finding = investigation.finding.model_copy(
        update={"evidence": [invented, within]}
    )
    result = assess(case, investigation, box)
    assert result.codes == ("EVIDENCE_NOT_VERIFIED",) and result.mitigation is None
    assert [check.ok for check in result.checks] == [False, True]  # 0.5% off is rounding
    assert "recomputed" in result.reasons[0].detail


def test_a_citation_of_a_call_that_was_never_made_is_not_verified(wh: Warehouse) -> None:
    case, investigation, box = outage(wh)
    ghost = Citation(call_id="c99", row="DE", column="window", value=0.3)
    nowhere = investigation.finding.evidence[0].model_copy(update={"row": "2031-01-01T00:00"})
    checks = verify(
        investigation.finding.model_copy(update={"evidence": [ghost, nowhere]}),
        investigation.trace,
        box,
        0.01,
    )
    assert [check.ok for check in checks] == [False, False]
    assert "no tool call c99" in checks[0].why and "has no number" in checks[1].why


def test_a_trace_cannot_vouch_for_itself(wh: Warehouse) -> None:
    # The recorded result is edited to agree with an invented number. The verifier runs
    # the call again and does not read the recording.
    case, investigation, box = outage(wh)
    cited = investigation.finding.evidence[0]
    call = next(c for c in investigation.trace if c.call_id == cited.call_id)
    for row in call.rows:
        if row["key"] == cited.row:
            row[cited.column] = 0.999
    investigation.finding = investigation.finding.model_copy(
        update={"evidence": [cited.model_copy(update={"value": 0.999})]}
    )
    assert codes(case, investigation, box) == ("EVIDENCE_NOT_VERIFIED",)


def test_a_change_that_does_not_exist_or_came_after_the_incident_began(wh: Warehouse) -> None:
    case, investigation, box = outage(wh)
    finding = investigation.finding
    investigation.finding = finding.model_copy(update={"change_id": "CHG-99999"})
    assert "CAUSE_NOT_IN_CHANGE_LOG" in codes(case, investigation, box)
    # Any change logged from the alert's hour to the end of the world will do: which background
    # changes a seed produces differs between operating systems, so no single hour is safe.
    later = wh.changes(at(10, 11), at(21))[0]["change_id"]
    investigation.finding = finding.model_copy(update={"change_id": later})
    assert "CAUSE_AFTER_ONSET" in codes(case, investigation, box)


def test_an_action_the_catalogue_does_not_allow_for_the_cause(wh: Warehouse) -> None:
    case, investigation, box = outage(wh)
    coupon = Mitigation(action="disable_coupon", target={"code": "SOMMER15"})
    investigation.finding = investigation.finding.model_copy(update={"mitigation": coupon})
    result = assess(case, investigation, box)
    assert "MITIGATION_NOT_ALLOWED" in result.codes and "TARGET_NOT_IN_EVIDENCE" in result.codes
    assert result.mitigation is None


def test_the_target_must_be_what_the_blamed_change_is_about(wh: Warehouse) -> None:
    case, investigation, box = outage(wh)
    other = FAILOVER.model_copy(update={"target": {"provider": "psp_beta", "country": "DE"}})
    investigation.finding = investigation.finding.model_copy(update={"mitigation": other})
    result = assess(case, investigation, box)
    assert result.codes == ("TARGET_NOT_IN_EVIDENCE",)
    assert "is not the entity of" in result.reasons[0].detail


def test_a_blamed_change_must_have_been_looked_up_in_this_investigation(wh: Warehouse) -> None:
    case, investigation, box = outage(wh)
    investigation.trace = [call for call in investigation.trace if call.name != "change_log"]
    result = assess(case, investigation, box)
    assert result.codes == ("TARGET_NOT_IN_EVIDENCE",)
    assert "never retrieved" in result.reasons[0].detail


def test_a_place_named_in_no_evidence_and_not_by_the_alert_is_not_a_target(wh: Warehouse) -> None:
    case, investigation, box = outage(wh)
    france = FAILOVER.model_copy(update={"target": {"provider": "psp_alpha", "country": "FR"}})
    investigation.finding = investigation.finding.model_copy(update={"mitigation": france})
    assert codes(case, investigation, box) == ("TARGET_NOT_IN_EVIDENCE",)


def test_without_a_blamed_change_the_target_must_be_seen_in_verified_evidence(
    wh: Warehouse,
) -> None:
    # Bot traffic has no change-log entry. The alert is on France; the channel is not
    # named by the alert, so it has to be a row that a verified citation points at.
    case = Case("INC-bot", "sessions", Slice.of(country="FR"), 1, wh.tick(at(9, 11)))
    investigation = ScriptInvestigator().investigate(case, Toolbox(wh, case.now))
    assert investigation.finding.cause == "bot_traffic"
    assert assess(case, investigation, Toolbox(wh, case.now)).reasons == ()
    kept = [c for c in investigation.finding.evidence if c.row != "affiliate"]
    investigation.finding = investigation.finding.model_copy(update={"evidence": kept})
    result = assess(case, investigation, Toolbox(wh, case.now))
    assert result.codes == ("TARGET_NOT_IN_EVIDENCE",) and "'affiliate'" in result.reasons[0].detail


def stamp(when: datetime) -> str:
    return f"{when:%Y-%m-%dT%H:%M}"


def cite(call: ToolResult, row: str, *columns: str) -> list[Citation]:
    """Citations of real cells: the value is what the tool returned."""
    return [
        Citation(call_id=call.call_id, row=row, column=column, value=call.value(row, column))
        for column in columns
    ]


def quiet_dip(wh: Warehouse) -> tuple[Case, Investigation, ToolResult]:
    """The unsafe proposal of a repeat run, rebuilt on a day of this world with no incident.

    The alert says payment success for the whole business is above expected. The
    finding blames a provider with no change-log entry behind it, with high
    confidence, and proposes a failover. Every number it cites is real, and every
    one of them shows a small dip: the opposite of what the alert reports.
    """
    case = Case("INC-quiet", "payment_success_rate", Slice(), 1, wh.tick(at(12, 14)))
    box = Toolbox(wh, case.now)
    window = {
        "metric": "payment_success_rate",
        "start": stamp(at(12, 12)),
        "end": stamp(at(12, 15)),
    }
    series = box.call("metric_series", window)
    providers = box.call("breakdown", {**window, "dimension": "provider"})
    countries = box.call(
        "breakdown", {**window, "dimension": "country", "slice": {"provider": "psp_alpha"}}
    )
    status = box.call(
        "change_log",
        {"start": stamp(at(11, 12)), "end": stamp(at(12, 15)), "kind": "provider_status"},
    )
    assert status.rows == []  # nothing in the change log stands behind the cause
    assert providers.value("psp_alpha", "change") < 0 and countries.value("DE", "change") < 0
    finding = Finding(
        cause="payment_provider_failure",
        affected_slice={"country": "DE", "provider": "psp_alpha"},
        evidence=[
            *cite(providers, "psp_alpha", "window", "baseline", "excess"),
            *cite(countries, "DE", "window", "baseline", "excess"),
            *cite(providers, "psp_gamma", "change"),
            *cite(series, stamp(at(12, 14)), "observed"),
        ],
        confidence="high",
        mitigation=FAILOVER,
        summary="Payment success dipped at psp_alpha in DE. No change-log entry explains it.",
    )
    return case, Investigation(finding, box.trace), providers


def test_a_cause_with_no_change_and_no_evidence_about_the_alert_is_refused(wh: Warehouse) -> None:
    case, investigation, _ = quiet_dip(wh)
    result = assess(case, investigation, Toolbox(wh, case.now))
    assert all(check.ok for check in result.checks)  # every cited number recomputes
    assert result.codes == ("CAUSE_NOT_SUPPORTED",) and result.mitigation is None
    assert "payment_success_rate above expected" in result.reasons[0].detail
    assert "psp_alpha" in result.reasons[0].detail
    # With nothing proposed there is nothing to refuse.
    investigation.finding = investigation.finding.model_copy(update={"mitigation": None})
    assert codes(case, investigation, Toolbox(wh, case.now)) == ()


def test_a_row_about_another_place_that_moved_the_alerts_way_is_not_support(wh: Warehouse) -> None:
    # A brief often cites the rows that did not move, for contrast. One of them moving
    # the way the alert did says nothing about the provider the proposal acts on.
    case, investigation, providers = quiet_dip(wh)
    assert providers.value("psp_delta", "change") > 0
    finding = investigation.finding
    investigation.finding = finding.model_copy(
        update={"evidence": [*finding.evidence, *cite(providers, "psp_delta", "change")]}
    )
    assert codes(case, investigation, Toolbox(wh, case.now)) == ("CAUSE_NOT_SUPPORTED",)


def unlogged_outage(wh: Warehouse) -> tuple[Case, Investigation]:
    """A provider failure the status feed never reported: the cause rests on a breakdown."""
    case = Case("INC-pay", "payment_success_rate", DE_CARD, -1, wh.tick(at(10, 11)))
    box = Toolbox(wh, case.now)
    window = {
        "metric": "payment_success_rate",
        "start": stamp(at(10, 10)),
        "end": stamp(at(10, 12)),
        "slice": DE_CARD.as_dict(),
    }
    series = box.call("metric_series", {**window, "start": stamp(at(10, 6))})
    providers = box.call("breakdown", {**window, "dimension": "provider"})
    finding = Finding(
        cause="payment_provider_failure",
        affected_slice={**DE_CARD.as_dict(), "provider": "psp_alpha"},
        evidence=[
            *cite(series, stamp(at(10, 9)), "observed"),  # the hour before: not out of range
            *cite(providers, "psp_alpha", "window", "baseline"),
        ],
        confidence="high",
        mitigation=FAILOVER,
        summary="German card payments fail at psp_alpha. No change-log entry: inferred.",
    )
    return case, Investigation(finding, box.trace)


def test_a_cause_with_no_change_passes_when_cited_evidence_ties_it_to_the_alert(
    wh: Warehouse,
) -> None:
    case, investigation = unlogged_outage(wh)
    result = assess(case, investigation, Toolbox(wh, case.now))
    assert result.reasons == () and result.mitigation == FAILOVER


def test_the_evidence_behind_a_cause_has_to_be_verified(wh: Warehouse) -> None:
    case, investigation = unlogged_outage(wh)
    finding = investigation.finding
    invented = [c.model_copy(update={"value": c.value * 1.05}) for c in finding.evidence]
    investigation.finding = finding.model_copy(update={"evidence": invented})
    result = assess(case, investigation, Toolbox(wh, case.now))
    assert result.codes == (
        "EVIDENCE_NOT_VERIFIED",
        "TARGET_NOT_IN_EVIDENCE",
        "CAUSE_NOT_SUPPORTED",
    )


def test_the_alerts_own_series_supports_a_proposal_for_the_alerts_own_slice(wh: Warehouse) -> None:
    # Bot traffic has no change-log entry and the alert is already on the place to
    # challenge. The alert's metric, cited for that place, is the support.
    where = Slice.of(country="FR", channel="affiliate")
    case = Case("INC-bot", "sessions", where, 1, wh.tick(at(9, 11)))
    box = Toolbox(wh, case.now)
    window = {"metric": "sessions", "start": stamp(at(9, 8)), "end": stamp(at(9, 12))}
    here = box.call("metric_series", {**window, "slice": where.as_dict()})
    germany = box.call("metric_series", {**window, "slice": {"country": "DE"}})
    challenge = Mitigation(action="enable_bot_challenge", target=where.as_dict())
    finding = Finding(
        cause="bot_traffic",
        affected_slice=where.as_dict(),
        evidence=cite(here, stamp(at(9, 11)), "observed", "expected"),
        confidence="high",
        mitigation=challenge,
        summary="Sessions tripled in French affiliate traffic. No change-log entry fits.",
    )
    investigation = Investigation(finding, box.trace)
    result = assess(case, investigation, Toolbox(wh, case.now))
    assert result.reasons == () and result.mitigation == challenge
    # The same proposal resting only on numbers about another country is refused.
    elsewhere = cite(germany, stamp(at(9, 11)), "observed", "expected")
    investigation.finding = finding.model_copy(update={"evidence": elsewhere})
    assert codes(case, investigation, Toolbox(wh, case.now)) == ("CAUSE_NOT_SUPPORTED",)


def test_only_a_high_confidence_finding_carries_a_proposal(wh: Warehouse) -> None:
    case, investigation, box = outage(wh)
    investigation.finding = investigation.finding.model_copy(update={"confidence": "medium"})
    assert codes(case, investigation, box) == ("LOW_CONFIDENCE",)
    # With nothing proposed there is nothing to hold.
    quiet = investigation.finding.model_copy(update={"confidence": "low", "mitigation": None})
    investigation.finding = quiet
    assert codes(case, investigation, box) == ()


def test_a_planned_event_explains_only_what_it_moves(wh: Warehouse) -> None:
    def held(metric: str, where: Slice, direction: int, day: int) -> tuple[str, ...]:
        case = Case("INC-x", metric, where, direction, wh.tick(at(day, 12)))
        investigation = Investigation(Finding(cause="unknown", confidence="low", summary="s"))
        return codes(case, investigation, Toolbox(wh, case.now))

    assert held("sessions", Slice(), 1, 16) == ("EXPECTED_EVENT",)  # a promotion lifts sessions
    assert held("sessions", Slice.of(country="NL"), 1, 16) == ("EXPECTED_EVENT",)
    # A promotion does not lower payment success, and does not lower sessions.
    assert held("payment_success_rate", DE_CARD, -1, 16) == ()
    assert held("sessions", Slice(), -1, 16) == ()
    # A holiday in Spain moves Spanish sessions either way, and nothing in France.
    assert held("sessions", Slice.of(country="ES"), -1, 18) == ("EXPECTED_EVENT",)
    assert held("sessions", Slice.of(country="FR"), -1, 18) == ()


def test_what_the_investigator_reports_about_itself_holds_the_finding(wh: Warehouse) -> None:
    case, investigation, box = outage(wh)
    investigation.flags, investigation.detail = ["BUDGET_EXHAUSTED"], "12 tool calls used"
    result = assess(case, investigation, box)
    assert result.codes == ("BUDGET_EXHAUSTED",) and result.mitigation is None


def test_severity_follows_the_money_lost_per_hour(wh: Warehouse) -> None:
    case, investigation, box = outage(wh)
    impact = impact_per_hour(case, box, ReviewPolicy())
    assert impact > 200  # a third of German card payments failing for two hours
    assert (
        assess(case, investigation, box, ReviewPolicy(page_eur_per_hour=impact - 1)).severity
        == "page"
    )
    assert (
        assess(case, investigation, box, ReviewPolicy(page_eur_per_hour=impact + 1)).severity
        == "ticket"
    )
    cheap = ReviewPolicy(page_eur_per_hour=impact * 10, ticket_eur_per_hour=impact + 1)
    assert assess(case, investigation, box, cheap).severity == "digest"


def test_bots_are_ticketed_and_planned_events_are_nobodys_incident(wh: Warehouse) -> None:
    bots = Case(
        "INC-bot", "sessions", Slice.of(country="FR", channel="affiliate"), 1, wh.tick(at(9, 11))
    )
    result = assess(
        bots, ScriptInvestigator().investigate(bots, Toolbox(wh, bots.now)), Toolbox(wh, bots.now)
    )
    assert (result.severity, result.owner) == ("ticket", "platform")
    promo = Case("INC-promo", "sessions", Slice(), 1, wh.tick(at(16, 12)))
    result = assess(
        promo,
        ScriptInvestigator().investigate(promo, Toolbox(wh, promo.now)),
        Toolbox(wh, promo.now),
    )
    assert (result.severity, result.owner) == ("none", "none")


def test_a_release_goes_to_the_team_of_its_platform() -> None:
    def release(platform: str) -> Finding:
        return Finding(
            cause="release_regression",
            affected_slice={"platform": platform},
            confidence="high",
            summary="s",
        )

    case = Case("INC-x", "checkout_conversion", Slice(), -1, 500)
    assert owner(release("app_android"), case) == "mobile"
    assert owner(release("web_mobile"), case) == "web"


def test_every_reason_code_states_why_it_exists() -> None:
    assert set(REASONS) == {
        "EVIDENCE_NOT_VERIFIED", "CAUSE_NOT_IN_CHANGE_LOG", "CAUSE_AFTER_ONSET",
        "MITIGATION_NOT_ALLOWED", "TARGET_NOT_IN_EVIDENCE", "CAUSE_NOT_SUPPORTED", "EXPECTED_EVENT",
        "LOW_CONFIDENCE", "BUDGET_EXHAUSTED", "INVESTIGATION_ERROR",
    }  # fmt: skip
    assert all(len(why) > 20 for why in REASONS.values())
