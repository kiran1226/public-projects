import json
from pathlib import Path

import pytest

from revwatch.models import Slice
from revwatch.scenarios import Label
from revwatch.tools import MAX_ROWS, TOOL_DEFINITIONS, Toolbox
from revwatch.warehouse import METRICS, Warehouse
from tests.conftest import at

PAYMENTS = "payment_success_rate"
OUTAGE = ("2026-06-11T10:00", "2026-06-11T16:00")  # the small world's card outage in Germany


@pytest.fixture()
def box(small_world: tuple[Path, list[Label]]) -> Toolbox:
    warehouse = Warehouse(small_world[0])
    return Toolbox(warehouse, now=warehouse.tick(at(10, 16)))


def test_metric_series_shows_observed_against_the_usual_value(box: Toolbox) -> None:
    result = box.call(
        "metric_series",
        {
            "metric": PAYMENTS,
            "start": OUTAGE[0],
            "end": OUTAGE[1],
            "slice": {"country": "DE", "payment_method": "card"},
        },
    )
    assert result.error is None and result.call_id == "c1"
    assert [row["key"] for row in result.rows][:2] == ["2026-06-11T10:00", "2026-06-11T11:00"]
    assert len(result.rows) == 6
    for row in result.rows:
        assert row["observed"] < 0.5 * row["expected"]  # success fell to about a third
        assert row["volume"] > 0


def test_metric_series_agrees_with_what_the_detectors_see(box: Toolbox) -> None:
    result = box.call(
        "metric_series",
        {
            "metric": "sessions",
            "start": "2026-06-08T00:00",
            "end": "2026-06-08T05:00",
            "slice": {"country": "FR"},
        },
    )
    panel = box.warehouse.panel(METRICS["sessions"])
    row = panel.slices.index(Slice.of(country="FR"))
    assert [r["observed"] for r in result.rows] == panel.num[row, 168:173].tolist()
    # One week of history: the expected value is last week's.
    assert [r["expected"] for r in result.rows] == panel.num[row, 0:5].tolist()


def test_breakdown_shows_which_value_carries_the_change(box: Toolbox) -> None:
    by_method = box.call(
        "breakdown",
        {
            "metric": PAYMENTS,
            "dimension": "payment_method",
            "start": OUTAGE[0],
            "end": OUTAGE[1],
            "slice": {"country": "DE"},
        },
    )
    rows = {row["key"]: row for row in by_method.rows}
    assert set(rows) == {"card", "wallet", "bank_transfer", "pay_later", "direct_debit"}
    assert min(rows, key=lambda k: rows[k]["excess"]) == "card"
    assert rows["card"]["change"] < -0.4 and abs(rows["wallet"]["change"]) < 0.05
    assert rows["card"]["window"] == pytest.approx(
        rows["card"]["baseline"] + rows["card"]["change"], rel=1e-4
    )
    by_provider = box.call(
        "breakdown",
        {
            "metric": PAYMENTS,
            "dimension": "provider",
            "start": OUTAGE[0],
            "end": OUTAGE[1],
            "slice": {"country": "DE"},
        },
    )
    worst = min(by_provider.rows, key=lambda row: row["excess"])
    assert worst["key"] == "psp_alpha"
    assert "baseline: 2026-06-04T10:00 to 2026-06-04T16:00" in by_provider.note


def test_a_count_is_broken_down_per_hour_so_that_windows_compare(box: Toolbox) -> None:
    args = {
        "metric": "sessions",
        "dimension": "country",
        "start": "2026-06-09T10:00",
        "end": "2026-06-09T12:00",
    }
    short = box.call(
        "breakdown",
        {**args, "baseline_start": "2026-06-02T10:00", "baseline_end": "2026-06-02T12:00"},
    )
    long = box.call(
        "breakdown",
        {**args, "baseline_start": "2026-06-02T10:00", "baseline_end": "2026-06-02T11:00"},
    )
    assert short.rows[0]["window"] == long.rows[0]["window"]  # per hour either way
    assert short.rows[0]["baseline"] != long.rows[0]["baseline"]


def test_change_log_finds_the_change_and_filters(box: Toolbox) -> None:
    window = {"start": "2026-06-11T08:00", "end": "2026-06-11T12:00"}
    everything = box.call("change_log", window)
    status = box.call("change_log", {**window, "kind": "provider_status"})
    assert 1 <= len(status.rows) < len(everything.rows)
    blamed = [row for row in status.rows if "elevated error rates" in row["note"]]
    assert len(blamed) == 1 and blamed[0]["entity"] == "psp_alpha" and blamed[0]["country"] == "DE"
    assert blamed[0]["key"].startswith("CHG-") and blamed[0]["time"] == "2026-06-11T09:45"
    # Entries scoped to another country cannot concern Germany.
    german = box.call("change_log", {**window, "scope": {"country": "DE"}})
    assert all(row.get("country", "DE") == "DE" for row in german.rows)
    assert blamed[0]["key"] in {row["key"] for row in german.rows}


def test_change_log_returns_the_newest_fifty_and_says_so(box: Toolbox) -> None:
    result = box.call("change_log", {"start": "2026-06-01T00:00", "end": "2026-06-11T16:00"})
    assert len(result.rows) == MAX_ROWS
    assert "earlier entries not shown" in result.note
    assert result.rows[-1]["time"] <= "2026-06-11T16:00"
    assert result.rows == sorted(result.rows, key=lambda row: row["time"])


def test_calendar_lists_planned_events(box: Toolbox) -> None:
    planned = box.call("calendar", {"start": "2026-06-16T00:00", "end": "2026-06-20T00:00"})
    assert [(row["kind"], row.get("country")) for row in planned.rows] == [
        ("planned_promotion", None),
        ("public_holiday", "ES"),
    ]
    assert box.call("calendar", {"start": OUTAGE[0], "end": OUTAGE[1]}).rows == []


def test_nothing_at_or_after_the_current_hour_can_be_read(box: Toolbox) -> None:
    late = box.call(
        "metric_series",
        {"metric": PAYMENTS, "start": "2026-06-11T15:00", "end": "2026-06-11T17:00"},
    )
    assert late.rows == [] and "the latest end is 2026-06-11T16:00" in (late.error or "")
    assert box.call(
        "breakdown",
        {
            "metric": PAYMENTS,
            "dimension": "country",
            "start": "2026-06-11T16:00",
            "end": "2026-06-11T18:00",
        },
    ).error
    # The change log simply stops at the current hour.
    changes = box.call("change_log", {"start": "2026-06-11T12:00", "end": "2026-06-13T00:00"})
    assert all(row["time"] < "2026-06-11T16:00" for row in changes.rows)


@pytest.mark.parametrize(
    ("name", "args", "message"),
    [
        (
            "metric_series",
            {"metric": "revenue", "start": OUTAGE[0], "end": OUTAGE[1]},
            "unknown metric",
        ),
        (
            "metric_series",
            {"metric": PAYMENTS, "start": "yesterday", "end": OUTAGE[1]},
            "ISO times",
        ),
        (
            "metric_series",
            {"metric": PAYMENTS, "start": OUTAGE[1], "end": OUTAGE[0]},
            "end must be after start",
        ),
        (
            "metric_series",
            {"metric": PAYMENTS, "start": "2026-06-02T00:00", "end": "2026-06-08T00:00"},
            "at most 50 hours",
        ),
        (
            "metric_series",
            {
                "metric": PAYMENTS,
                "start": OUTAGE[0],
                "end": OUTAGE[1],
                "slice": {"channel": "email"},
            },
            "has no dimension 'channel'",
        ),
        ("metric_series", {"metric": PAYMENTS}, "missing"),
        (
            "breakdown",
            {"metric": "sessions", "dimension": "provider", "start": OUTAGE[0], "end": OUTAGE[1]},
            "cannot be split by 'provider'",
        ),
        (
            "breakdown",
            {
                "metric": "sessions",
                "dimension": "country",
                "start": "2026-06-02T00:00",
                "end": "2026-06-02T05:00",
            },
            "give a baseline window",
        ),
        (
            "change_log",
            {"start": OUTAGE[0], "end": OUTAGE[1], "scope": {"region": "EU"}},
            "unknown scope",
        ),
        ("run_sql", {"query": "DROP TABLE orders"}, "unknown tool"),
        (
            "metric_series",
            {"metric": PAYMENTS, "start": OUTAGE[0], "end": OUTAGE[1], "slice": "country=DE"},
            "slice must be an object",
        ),
        (
            "breakdown",
            {
                "metric": PAYMENTS,
                "dimension": "platform",
                "start": OUTAGE[0],
                "end": OUTAGE[1],
                "slice": ["country", "DE"],
            },
            "slice must be an object",
        ),
        (
            "change_log",
            {"start": OUTAGE[0], "end": OUTAGE[1], "scope": ["DE"]},
            "scope must be an object",
        ),
        (
            "change_log",
            {"start": OUTAGE[0], "end": OUTAGE[1], "scope": "country=DE"},
            "scope must be an object",
        ),
        (
            "metric_series",
            {"metric": PAYMENTS, "start": OUTAGE[0], "end": OUTAGE[1], "slice": {"country": 7}},
            "unknown country value",
        ),
    ],
)
def test_a_bad_call_is_an_error_result_not_an_exception(
    box: Toolbox, name: str, args: dict[str, object], message: str
) -> None:
    result = box.call(name, args)
    assert result.rows == [] and message in (result.error or "")
    assert json.loads(result.text()) == {"call_id": "c1", "error": result.error}


def test_every_call_is_recorded_and_can_be_run_again(box: Toolbox) -> None:
    first = box.call("calendar", {"start": OUTAGE[0], "end": OUTAGE[1]})
    second = box.call(
        "breakdown",
        {"metric": PAYMENTS, "dimension": "country", "start": OUTAGE[0], "end": OUTAGE[1]},
    )
    assert [call.call_id for call in box.trace] == ["c1", "c2"] == [first.call_id, second.call_id]
    again = box.rerun(second)
    assert again == second and len(box.trace) == 2
    assert (
        second.value("DE", "window") == next(r for r in second.rows if r["key"] == "DE")["window"]
    )
    assert second.value("XX", "window") is None and second.value("DE", "nothing") is None
    assert json.loads(second.text())["rows"] == second.rows


def test_the_tool_list_has_no_tool_that_writes() -> None:
    names = [tool["name"] for tool in TOOL_DEFINITIONS]
    assert names == ["metric_series", "breakdown", "change_log", "calendar", "submit_finding"]
    for tool in TOOL_DEFINITIONS:
        assert tool["input_schema"]["type"] == "object"


def test_the_finding_schema_the_model_reads_is_self_contained() -> None:
    schema = TOOL_DEFINITIONS[-1]["input_schema"]
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text and '"title"' not in text
    assert schema["required"] == ["cause", "confidence", "summary"]
    assert schema["additionalProperties"] is False
    citation = schema["properties"]["evidence"]["items"]
    assert citation["required"] == ["call_id", "row", "column", "value"]
    target = schema["properties"]["mitigation"]["anyOf"][0]["properties"]["target"]
    assert target["additionalProperties"] == {"type": "string"}
    assert "unknown" in schema["properties"]["cause"]["enum"]
