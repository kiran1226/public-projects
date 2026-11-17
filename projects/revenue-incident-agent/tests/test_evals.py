from datetime import datetime, timedelta

from revwatch.evals import WHY, confirm_from_labels, matches, render_report, score_detection
from revwatch.models import Incident, Slice
from revwatch.pipeline import DetectionRun
from revwatch.scenarios import Label

START = datetime(2026, 6, 1)
DE_CARD = {"country": "DE", "payment_method": "card"}


def label(scenario_id: str, kind: str, lo: int, hi: int, where: dict[str, str]) -> Label:
    return Label(
        scenario_id=scenario_id,
        kind=kind,
        cause="unknown",  # type: ignore[arg-type]
        start=START + timedelta(hours=lo),
        end=START + timedelta(hours=hi),
        slice=where,
        labelled=True,
        change_id=None,
        mitigation=None,
    )


def incident(incident_id: str, opened: int, where: Slice) -> Incident:
    return Incident(incident_id, opened, "payment_success_rate", where, -1, opened)


LABELS = [
    label("pay", "incident", 1400, 1406, DE_CARD),
    label("rel", "incident", 1500, 1510, {"platform": "app_android"}),
    label("promo", "benign", 1450, 1470, {}),
]
RUN = DetectionRun(
    detector="x",
    incidents=[
        incident("e", 1300, Slice.from_dict(DE_CARD)),  # before scoring starts: ignored
        incident("a", 1402, Slice.from_dict(DE_CARD)),  # detects pay after 2 hours, exact slice
        incident("b", 1404, Slice.of(country="DE")),  # same incident again: duplicate
        incident("f", 1407, Slice.of(country="FR")),  # in pay's grace window, unrelated slice
        incident("c", 1455, Slice.of(country="FR")),  # on the planned promotion
        incident("d", 1600, Slice.of(country="ES")),  # on nothing
    ],
    n_hours=1344 + 336,
    scored_from=1344,
)


def test_matching_needs_the_window_and_a_related_slice() -> None:
    pay = LABELS[0]
    assert matches(incident("i", 1400, Slice.of(country="DE")), pay, START)
    assert matches(
        incident("i", 1408, Slice.from_dict(DE_CARD)), pay, START
    )  # inside the grace period
    assert not matches(incident("i", 1409, Slice.from_dict(DE_CARD)), pay, START)
    assert not matches(incident("i", 1399, Slice.from_dict(DE_CARD)), pay, START)
    assert not matches(incident("i", 1402, Slice.of(country="FR")), pay, START)
    assert matches(incident("i", 1402, Slice()), pay, START)  # the overall series is an ancestor


def test_detection_is_counted_per_incident() -> None:
    report = score_detection(RUN, LABELS, START)
    assert (report.n_incidents, report.detected) == (2, 1)
    assert report.false_incidents == 3
    assert report.duplicates == 1
    assert report.median_hours_to_detect == 3.0
    assert report.localised == 1
    assert report.weeks == 2.0
    by_id = {r.scenario_id: r for r in report.results}
    assert (by_id["pay"].alerts, by_id["pay"].hours_to_detect, by_id["pay"].localised) == (
        2,
        3.0,
        True,
    )
    assert (by_id["rel"].alerts, by_id["rel"].hours_to_detect, by_id["rel"].localised) == (
        0,
        None,
        None,
    )
    assert by_id["promo"].alerts == 1


def test_false_incidents_on_a_look_alike_are_counted_apart_from_noise() -> None:
    report = score_detection(RUN, LABELS, START)
    assert report.false_on_benign == 1  # c, on the promotion; f and d are on nothing
    text = render_report([report], "dev", "abc123")
    assert "| False incidents on a benign look-alike | 1 of 3 |" in text


def test_a_broad_alert_detects_but_does_not_localise() -> None:
    run = DetectionRun("x", [incident("a", 1401, Slice.of(country="DE"))], 1344 + 336, 1344)
    report = score_detection(run, LABELS, START)
    assert report.detected == 1 and report.localised == 0


def test_no_detections_gives_no_median() -> None:
    report = score_detection(DetectionRun("x", [], 1344 + 336, 1344), LABELS, START)
    assert report.detected == 0 and report.median_hours_to_detect is None


def test_confirm_from_labels_accepts_only_real_incidents() -> None:
    confirm = confirm_from_labels(LABELS, START)
    assert confirm(incident("a", 1402, Slice.from_dict(DE_CARD)))
    assert not confirm(incident("c", 1455, Slice.of(country="FR")))


def test_report_states_why_each_metric_was_chosen() -> None:
    text = render_report([score_detection(RUN, LABELS, START)], "dev", "abc123")
    assert "| Metric | `x` | Why this metric |" in text
    assert "| Incident recall | 1 of 2 |" in text
    assert "| False incidents per week | 1.5 |" in text
    for _, reason in WHY.values():
        assert reason in text
    assert "## Metrics not used" in text and "AUROC" in text
    assert "| pay | incident | 3 h, exact slice |" in text
    assert "| rel | incident | missed |" in text
    assert "| promo | benign | 1 alert |" in text
    assert "| checkout_conversion | Orders / sessions |" in text


def test_a_missed_incident_is_marked_buried_when_an_older_alert_was_still_open() -> None:
    # An alert on Germany opened long before the outage and is still alarming during it.
    old = Incident("old", 1350, "payment_success_rate", Slice.of(country="DE"), -1, 1403)
    old.members = {("payment_success_rate", Slice.of(country="DE"), -1)}
    report = score_detection(DetectionRun("x", [old], 1344 + 336, 1344), LABELS, START)
    by_id = {r.scenario_id: r for r in report.results}
    assert report.detected == 0 and report.buried == 1
    assert by_id["pay"].buried and not by_id["rel"].buried
    text = render_report([report], "dev", "abc123")
    assert "| Missed while an older alert was open | 1 |" in text
    assert "| pay | incident | missed, older alert open |" in text


def test_an_older_alert_that_went_quiet_or_is_unrelated_does_not_bury() -> None:
    quiet = Incident("q", 1350, "payment_success_rate", Slice.of(country="DE"), -1, 1390)
    quiet.members = {("payment_success_rate", Slice.of(country="DE"), -1)}
    other = Incident("o", 1350, "payment_success_rate", Slice.of(country="FR"), -1, 1403)
    other.members = {("payment_success_rate", Slice.of(country="FR"), -1), ("sessions", Slice(), 1)}
    report = score_detection(DetectionRun("x", [quiet, other], 1344 + 336, 1344), LABELS, START)
    assert report.buried == 0


def test_time_to_detect_counts_the_hour_the_alert_needs_to_be_complete() -> None:
    # Onset at tick 1400. The alert opens on tick 1402, whose data is complete at 1403:00.
    report = score_detection(RUN, LABELS, START)
    assert {r.scenario_id: r.hours_to_detect for r in report.results}["pay"] == 3.0


def test_merged_alerts_are_scored_as_raised() -> None:
    survivor = incident("s", 1401, Slice.of(country="ES"))
    merged = incident("m", 1402, Slice.from_dict(DE_CARD))
    merged.merged_into, merged.closed_tick = "s", 1403
    report = score_detection(DetectionRun("x", [survivor, merged], 1344 + 336, 1344), LABELS, START)
    assert report.detected == 1  # the merged alert found the outage
    assert report.false_incidents == 1  # the survivor is on nothing


def test_report_lists_hours_that_were_not_scored() -> None:
    gaps = {"sessions": (1500, 1501), "checkout_conversion": (1500, 1501)}
    run = DetectionRun("x", [], 1344 + 336, 1344, gaps)
    text = render_report([score_detection(run, LABELS, START)], "dev", "abc123")
    assert "## Hours not scored" in text
    assert "- sessions: 2 hours, 2026-08-02 12:00 to 2026-08-02 13:00" in text
    quiet = render_report([score_detection(RUN, LABELS, START)], "dev", "abc123")
    assert "## Hours not scored\n\nNone." in quiet


def test_report_can_show_the_same_scenarios_on_other_seeds() -> None:
    main = score_detection(RUN, LABELS, START)
    text = render_report([main], "dev", "abc123", other_seeds={7: [main]})
    assert "## Other seeds" in text
    assert "| 7 | 1 of 2, 1.5 per week |" in text


def test_late_data_only_counts_alerts_on_metrics_built_on_the_late_table() -> None:
    late = label("late", "benign", 1450, 1452, {}).model_copy(update={"drop_table": "traffic"})

    def alert(metric: str) -> Incident:
        return Incident("x", 1451, metric, Slice.of(country="DE"), -1, 1451)

    assert matches(alert("sessions"), late, START)
    assert matches(alert("checkout_conversion"), late, START)  # its denominator is traffic
    # A payment alert in the same two hours has nothing to do with the late traffic table.
    assert not matches(alert("payment_success_rate"), late, START)


def test_an_alert_on_a_metric_the_incident_does_not_move_does_not_detect_it() -> None:
    outage = label("pay", "incident", 1400, 1406, DE_CARD).model_copy(
        update={"moves": {"payment_success_rate": -1, "checkout_conversion": -1}}
    )

    def alert(metric: str, direction: int, where: Slice) -> Incident:
        return Incident("x", 1402, metric, where, direction, 1402)

    assert matches(alert("payment_success_rate", -1, Slice.from_dict(DE_CARD)), outage, START)
    assert matches(alert("checkout_conversion", -1, Slice.of(country="DE")), outage, START)
    # More sessions overall, in the same hours: the overall series is related to every
    # slice, but a card outage does not raise sessions.
    assert not matches(alert("sessions", 1, Slice()), outage, START)
    # Payment success going up is not this incident either.
    assert not matches(alert("payment_success_rate", 1, Slice.from_dict(DE_CARD)), outage, START)
    report = score_detection(
        DetectionRun("x", [alert("sessions", 1, Slice())], 1344 + 336, 1344), [outage], START
    )
    assert (report.detected, report.false_incidents) == (0, 1)


def test_a_look_alike_only_explains_alerts_on_the_metrics_it_moves() -> None:
    shift = label("shift", "benign", 1400, 1400 + 7 * 168, {"country": "PL"}).model_copy(
        update={"moves": {"sessions": 1}}
    )
    payments = Incident("x", 1900, "payment_success_rate", Slice(), -1, 1903)
    report = score_detection(DetectionRun("x", [payments], 3000, 1344), [shift], START)
    assert (report.false_incidents, report.false_on_benign) == (1, 0)
