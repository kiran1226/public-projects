from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from revwatch.investigate.base import Case
from revwatch.investigate.script import ScriptInvestigator
from revwatch.models import Slice
from revwatch.scenarios import Label
from revwatch.tools import Toolbox
from revwatch.warehouse import Warehouse
from tests.conftest import at

DE_CARD = Slice.of(country="DE", payment_method="card")
FR_AFFILIATE = Slice.of(country="FR", channel="affiliate")


@pytest.fixture()
def wh(small_world: tuple[Path, list[Label]]) -> Warehouse:
    return Warehouse(small_world[0])


def investigate(wh: Warehouse, metric: str, where: Slice, direction: int, day: int, hour: int):  # type: ignore[no-untyped-def]
    case = Case("INC-test", metric, where, direction, wh.tick(at(day, hour)))
    return ScriptInvestigator().investigate(case, Toolbox(wh, case.now))


def test_a_card_outage_is_traced_to_the_provider_status_entry(wh: Warehouse) -> None:
    result = investigate(wh, "payment_success_rate", DE_CARD, -1, 10, 11)
    finding = result.finding
    blamed = wh.change(finding.change_id or "")
    assert finding.cause == "payment_provider_failure"
    assert blamed is not None and blamed["entity"] == "psp_alpha"
    assert finding.mitigation is not None
    assert finding.mitigation.action == "failover_payment_provider"
    assert finding.mitigation.target == {"provider": "psp_alpha", "country": "DE"}
    assert finding.confidence == "high"
    assert (
        finding.affected_slice["country"] == "DE"
        and finding.affected_slice["payment_method"] == "card"
    )
    assert "2026-06-11T10:00" in finding.summary  # when it began
    assert result.tool_calls <= 12 and result.cost_usd == 0.0


def test_the_same_outage_alerted_on_a_broader_slice_is_narrowed_to_card(wh: Warehouse) -> None:
    finding = investigate(wh, "payment_success_rate", Slice.of(country="DE"), -1, 10, 11).finding
    assert finding.affected_slice == {"country": "DE", "payment_method": "card"}
    assert finding.cause == "payment_provider_failure"


def test_every_number_it_cites_is_in_its_own_trace(wh: Warehouse) -> None:
    result = investigate(wh, "payment_success_rate", DE_CARD, -1, 10, 11)
    assert len(result.finding.evidence) >= 3
    by_id = {call.call_id: call for call in result.trace}
    for citation in result.finding.evidence:
        assert by_id[citation.call_id].value(citation.row, citation.column) == citation.value


def test_bot_traffic_is_named_without_a_change_log_entry(wh: Warehouse) -> None:
    finding = investigate(wh, "sessions", FR_AFFILIATE, 1, 9, 11).finding
    assert (finding.cause, finding.change_id, finding.confidence) == ("bot_traffic", None, "high")
    assert finding.mitigation is not None
    assert finding.mitigation.action == "enable_bot_challenge"
    assert finding.mitigation.target == {"country": "FR", "channel": "affiliate"}


def test_an_alert_inside_a_planned_event_is_an_expected_event(wh: Warehouse) -> None:
    result = investigate(wh, "sessions", Slice(), 1, 16, 12)
    assert result.finding.cause == "expected_event" and result.finding.mitigation is None
    assert result.tool_calls == 1  # the calendar answers it
    # A holiday in Spain does not explain an alert about France.
    assert (
        investigate(wh, "sessions", Slice.of(country="FR"), -1, 18, 12).finding.cause
        != "expected_event"
    )


class QuietChangeLog(Warehouse):
    """The same shop with an empty change log.

    The background changes a seed produces differ between operating systems, so a test that
    needs "nothing to blame" cannot rely on a quiet hour in the generated log.
    """

    def changes(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return []


def test_with_nothing_to_blame_it_says_unknown_and_proposes_nothing(
    small_world: tuple[Path, list[Label]],
) -> None:
    quiet = QuietChangeLog(small_world[0])
    finding = investigate(quiet, "gross_margin_rate", Slice.of(country="AT"), -1, 12, 14).finding
    assert (finding.cause, finding.change_id, finding.mitigation) == ("unknown", None, None)
    assert finding.confidence == "low" and "Cause not found" in finding.summary


def test_a_change_of_a_kind_that_cannot_move_the_metric_that_way_is_not_blamed(
    wh: Warehouse,
) -> None:
    # The hours of the card outage hold the provider entry, price updates and releases.
    # None of them makes more people visit: a rise in sessions has no cause in the log.
    finding = investigate(wh, "sessions", Slice.of(country="DE"), 1, 10, 11).finding
    assert (finding.cause, finding.change_id, finding.mitigation) == ("unknown", None, None)
    # Payment success going up is not a provider failure either.
    up = investigate(wh, "payment_success_rate", DE_CARD, 1, 10, 11).finding
    assert up.change_id is None and up.mitigation is None
