from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import pytest

from revwatch import pipeline
from revwatch.detectors.base import Scores
from revwatch.detectors.gbm import GbmDetector
from revwatch.detectors.seasonal import SeasonalDetector
from revwatch.evals import confirm_from_labels, score_detection
from revwatch.models import BoolArray, Incident, Slice
from revwatch.pipeline import DetectionRun, numerator_deviation, read, run_detection
from revwatch.policy import DetectionPolicy
from revwatch.scenarios import Effect, Label, Scenario
from revwatch.slices import enumerate_slices
from revwatch.warehouse import CALENDAR_KINDS, METRICS, MetricDef, Panel, Warehouse
from revwatch.world import WorldConfig, generate_world
from tests.conftest import at

N_HOURS = 400
POLICY = DetectionPolicy(warmup_hours=100, fold_hours=50)
PAYMENTS = "payment_success_rate"
DE = Slice.of(country="DE")


class FaintDetector:
    """Reports the same small p-value, below expected, for one slice at the given ticks."""

    name = "faint"
    windows: tuple[int, ...] = (1,)

    def __init__(self, metric: str, where: Slice, ticks: Sequence[int], pvalue: float) -> None:
        self.metric, self.where, self.ticks, self.pvalue = metric, where, ticks, pvalue

    def score(
        self,
        panel: Panel,
        flags: Mapping[str, BoolArray],
        start: int,
        stop: int,
        exclude: BoolArray,
    ) -> Scores:
        shape = (len(panel.slices), stop - start)
        pvalue, strength = np.ones(shape), np.full(shape, 0.1)
        if panel.metric.name == self.metric:
            row = panel.slices.index(self.where)
            for tick in self.ticks:
                if start <= tick < stop:
                    pvalue[row, tick - start], strength[row, tick - start] = self.pvalue, -3.0
        return Scores(expected=panel.value[:, start:stop].copy(), pvalue=pvalue, strength=strength)


class StubWorld:
    """Flat panels for every metric, with one low-volume slice."""

    n_hours = N_HOURS
    world_hash = "stub"

    def panel(self, metric: MetricDef) -> Panel:
        slices = enumerate_slices(metric.dims)
        den = np.full((len(slices), N_HOURS), 10_000.0)
        den[slices.index(Slice.of(country="AT"))] = 10.0
        num = den * 0.5
        if metric.kind == "count":
            den = np.ones_like(num)
        return Panel(metric, tuple(slices), num, den)

    def calendar_flags(self, slices: Sequence[Slice]) -> dict[str, BoolArray]:
        return {kind: np.zeros((len(slices), N_HOURS), dtype=bool) for kind in CALENDAR_KINDS}


class ScriptedDetector:
    """Reports p = 0 below expected at the scripted (metric, slice, tick) cells."""

    name = "scripted"
    windows: tuple[int, ...] = (1,)

    def __init__(self, hits: dict[tuple[str, Slice], list[int]], blank: Sequence[int] = ()) -> None:
        self.hits = hits
        self.blank = set(blank)
        self.excluded: list[tuple[str, int, int]] = []  # (metric, start, cells excluded)

    def score(
        self,
        panel: Panel,
        flags: Mapping[str, BoolArray],
        start: int,
        stop: int,
        exclude: BoolArray,
    ) -> Scores:
        self.excluded.append((panel.metric.name, start, int(exclude.sum())))
        shape = (len(panel.slices), stop - start)
        pvalue, strength = np.ones(shape), np.full(shape, 0.1)
        for (metric, where), ticks in self.hits.items():
            if metric != panel.metric.name:
                continue
            row = panel.slices.index(where)
            for tick in ticks:
                if start <= tick < stop:
                    pvalue[row, tick - start], strength[row, tick - start] = 0.0, -5.0
        for tick in self.blank:
            if start <= tick < stop:
                pvalue[:, tick - start] = np.nan
        return Scores(expected=panel.value[:, start:stop].copy(), pvalue=pvalue, strength=strength)


def run(detector: ScriptedDetector, **kwargs: object) -> DetectionRun:
    return run_detection(StubWorld(), detector, POLICY, **kwargs)  # type: ignore[arg-type]


def test_nothing_alarms_on_quiet_data() -> None:
    result = run(ScriptedDetector({}))
    assert result.incidents == []
    assert (result.detector, result.n_hours, result.scored_from) == ("scripted", N_HOURS, 100)


def test_one_passing_tick_is_not_enough() -> None:
    assert run(ScriptedDetector({(PAYMENTS, DE): [200]})).incidents == []


def test_persistence_carries_across_a_fold_boundary() -> None:
    # Folds start at 100, 150, 200, ... so ticks 149 and 150 are in different folds.
    result = run(ScriptedDetector({(PAYMENTS, DE): [149, 150]}))
    assert [(i.opened_tick, i.metric, i.slice) for i in result.incidents] == [(150, PAYMENTS, DE)]


def test_an_incident_open_at_the_end_of_the_world_is_reported() -> None:
    result = run(ScriptedDetector({(PAYMENTS, DE): [398, 399]}))
    assert len(result.incidents) == 1
    assert result.incidents[0].opened_tick == 399 and result.incidents[0].closed_tick is None


def test_a_slice_below_the_volume_floor_never_alarms() -> None:
    low = Slice.of(country="AT")
    assert run(ScriptedDetector({(PAYMENTS, low): [200, 201, 202]})).incidents == []


def test_a_tick_with_nothing_scored_is_skipped() -> None:
    result = run(ScriptedDetector({(PAYMENTS, DE): [200, 201]}, blank=[201]))
    assert result.incidents == []


def test_confirmed_incidents_are_excluded_from_later_folds() -> None:
    detector = ScriptedDetector({(PAYMENTS, DE): [120, 121, 122]})
    run(detector, confirm=lambda incident: True)
    payments = {start: cells for metric, start, cells in detector.excluded if metric == PAYMENTS}
    assert payments[100] == 0
    # Opened at 121, last alarm at 122: hours 118..122 of one slice.
    assert payments[150] == 5 and payments[350] == 5
    other = [cells for metric, _, cells in detector.excluded if metric != PAYMENTS]
    assert set(other) == {0}


def test_unconfirmed_incidents_stay_in_the_history() -> None:
    detector = ScriptedDetector({(PAYMENTS, DE): [120, 121, 122]})
    run(detector, confirm=lambda incident: False)
    assert {cells for _, _, cells in detector.excluded} == {0}


def test_progress_is_reported_once_per_fold() -> None:
    messages: list[str] = []
    run(ScriptedDetector({}), progress=messages.append)
    assert len(messages) == 6
    assert messages[0].startswith("scripted: scored hours 100-150 of 400")


def test_numerator_deviation_uses_the_expected_rate() -> None:
    panel = StubWorld().panel(METRICS[PAYMENTS])
    expected = np.full(panel.num.shape, 0.6)
    deviation = numerator_deviation(panel, expected, tick=10, window=3)
    row = panel.slices.index(DE)
    assert deviation[row] == pytest.approx(3 * (5_000.0 - 0.6 * 10_000.0))


@pytest.fixture(scope="module")
def outage_world(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[Label]]:
    scenarios = [
        Scenario(
            id="pay",
            kind="incident",
            cause="payment_provider_failure",
            start=at(60, 10),
            end=at(60, 16),
            slice={"country": "DE", "payment_method": "card"},
            effects=[Effect(param="payment_success_mult", value=0.3)],
        )
    ]
    path = tmp_path_factory.mktemp("outage") / "outage.duckdb"
    return path, generate_world(WorldConfig(seed=3, days=70), scenarios, path)


def detect(
    world: tuple[Path, list[Label]], detector: SeasonalDetector | GbmDetector
) -> tuple[list[Incident], float | None]:
    path, labels = world
    with Warehouse(path) as warehouse:
        confirm = confirm_from_labels(labels, warehouse.start)
        result = run_detection(warehouse, detector, confirm=confirm)
        report = score_detection(result, labels, warehouse.start)
    assert report.detected == 1
    return result.incidents, report.median_hours_to_detect


def test_seasonal_detector_finds_a_payment_outage_in_a_generated_world(
    outage_world: tuple[Path, list[Label]],
) -> None:
    incidents, hours = detect(outage_world, SeasonalDetector())
    assert hours is not None and hours <= 3
    again, _ = detect(outage_world, SeasonalDetector())
    assert [i.incident_id for i in incidents] == [i.incident_id for i in again]


def test_gbm_detector_finds_a_payment_outage_in_a_generated_world(
    outage_world: tuple[Path, list[Label]],
) -> None:
    _, hours = detect(outage_world, GbmDetector())
    assert hours is not None and hours <= 3


def test_incidents_that_merge_both_stay_in_the_run() -> None:
    both = Slice.of(country="DE", platform="app_android")
    hits = {
        (PAYMENTS, DE): [200, 201, 202, 203, 204],
        # Conversion falls on Android in Germany: a second alert, in another place.
        ("checkout_conversion", both): [201, 202, 203, 204],
        # Then in all of Germany: the same place as the payments alert.
        ("checkout_conversion", DE): [203, 204],
    }
    result = run(ScriptedDetector(hits))
    assert sorted(i.opened_tick for i in result.incidents) == [201, 202]
    merged = [i for i in result.incidents if i.merged_into is not None]
    # Germany is out of its band at 203, a tick before it alarms; that already explains
    # the narrower slice, so the two alerts become one then.
    assert len(merged) == 1 and merged[0].opened_tick == 202 and merged[0].closed_tick == 203


class GappyWorld(StubWorld):
    """Traffic has no rows for two hours, so sessions and conversion cannot be scored there."""

    def panel(self, metric: MetricDef) -> Panel:
        panel = super().panel(metric)
        if "traffic" in (metric.num[0], metric.den[0] if metric.den else ""):
            panel.num[:, 210:212] = np.nan
            panel.den[:, 210:212] = np.nan
        return panel


def test_hours_without_data_are_recorded_as_gaps() -> None:
    result = run_detection(GappyWorld(), ScriptedDetector({}), POLICY)  # type: ignore[arg-type]
    assert result.gaps == {"sessions": (210, 211), "checkout_conversion": (210, 211)}
    assert run(ScriptedDetector({})).gaps == {}


def test_the_grouper_reads_deviation_expected_and_volume_over_the_same_ticks() -> None:
    panel = StubWorld().panel(METRICS[PAYMENTS])  # 5,000 successes of 10,000 attempts an hour
    panel.num[:, 9], panel.den[:, 9] = np.nan, np.nan  # one hour without data
    expected = np.full(panel.num.shape, 0.6)
    strength = np.full(panel.num.shape, -2.0)
    strength[:, 8] = np.nan
    pvalue = np.full(panel.num.shape, 0.01)
    reading = read(panel, expected, strength, pvalue, tick=10, window=4)
    row = panel.slices.index(DE)
    # Ticks 7, 8 and 10 count; tick 9 has no data.
    assert reading.deviation[row] == pytest.approx(3 * (5_000.0 - 6_000.0))
    assert reading.expected[row] == pytest.approx(3 * 6_000.0)
    assert reading.volume is not None and reading.volume[row] == pytest.approx(30_000.0)
    assert reading.strength[row] == pytest.approx(-2.0)
    assert reading.pvalue[row] == 0.01
    sessions = StubWorld().panel(METRICS["sessions"])
    assert read(sessions, sessions.num, strength, pvalue, tick=10, window=4).volume is None


def test_a_detector_that_tests_longer_windows_than_the_evidence_is_refused() -> None:
    detector = ScriptedDetector({})
    detector.windows = (1, 8)
    with pytest.raises(ValueError, match="evidence_hours"):
        run(detector)


def test_the_false_alarm_level_is_shared_out_between_the_metrics() -> None:
    # Payment success has 90 series, 89 of them scored here. One of them alone passes
    # Benjamini-Hochberg at level q when p <= q / 89. With five metrics sharing 0.05 that
    # is 0.00011; with 0.05 for each it would be 0.00056.
    def alerts(pvalue: float) -> int:
        detector = FaintDetector(PAYMENTS, DE, [200, 201], pvalue)
        return len(run_detection(StubWorld(), detector, POLICY).incidents)  # type: ignore[arg-type]

    assert alerts(0.0001) == 1
    assert alerts(0.0003) == 0


def test_the_grouper_reads_evidence_over_the_policy_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows: set[int] = set()
    original = pipeline.read

    def spy(*args: object, **kwargs: object) -> object:
        windows.add(args[5] if len(args) > 5 else kwargs["window"])  # type: ignore[arg-type]
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline, "read", spy)
    run(ScriptedDetector({(PAYMENTS, DE): [200, 201]}))
    assert windows == {POLICY.evidence_hours}
