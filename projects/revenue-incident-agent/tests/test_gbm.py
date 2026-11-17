import warnings

import numpy as np
import pytest
from sklearn.ensemble import HistGradientBoostingRegressor

from revwatch.detectors.gbm import GbmDetector, bucket_count
from revwatch.warehouse import Panel
from tests.helpers import (
    mixed_volume_counts,
    no_exclusions,
    no_flags,
    synthetic_panel,
    with_factor,
)

START, STOP = 1344, 1512


def detector() -> GbmDetector:
    return GbmDetector(n_tests=90)


def test_flags_an_injected_drop() -> None:
    panel = with_factor(synthetic_panel(), 5, 1400, 1410, 0.4)
    scores = detector().score(panel, no_flags(panel), START, STOP, no_exclusions(panel))
    window = slice(1400 - START, 1410 - START)
    assert scores.pvalue.shape == (90, STOP - START)
    assert (scores.pvalue[5, window] < 0.01).all()
    assert (scores.strength[5, window] < -1.0).all()
    assert 0.03 < float(np.mean(scores.expected[5, window])) < 0.08


def test_p_values_are_calibrated_on_quiet_data() -> None:
    panel = synthetic_panel()
    scores = detector().score(panel, no_flags(panel), START, STOP, no_exclusions(panel))
    p = scores.pvalue[np.isfinite(scores.pvalue)]
    assert p.size == 90 * (STOP - START)
    assert 0.02 < float((p < 0.05).mean()) < 0.09
    assert float((p < 0.001).mean()) < 0.003  # the fitted tail is not overconfident


def test_scores_do_not_depend_on_later_data() -> None:
    base = synthetic_panel()
    changed = with_factor(base, slice(None), 1400, base.num.shape[1], 3.0)
    a = detector().score(base, no_flags(base), START, 1400, no_exclusions(base))
    b = detector().score(changed, no_flags(changed), START, 1400, no_exclusions(changed))
    np.testing.assert_array_equal(a.pvalue, b.pvalue)
    np.testing.assert_array_equal(a.expected, b.expected)


def test_same_inputs_give_the_same_scores() -> None:
    panel = synthetic_panel()
    a = detector().score(panel, no_flags(panel), START, STOP, no_exclusions(panel))
    b = detector().score(panel, no_flags(panel), START, STOP, no_exclusions(panel))
    np.testing.assert_array_equal(a.pvalue, b.pvalue)


def test_too_little_history_is_a_clear_error() -> None:
    panel = synthetic_panel()
    assert GbmDetector.HISTORY_NEEDED == 1176
    with pytest.raises(ValueError, match="gbm needs 1176 hours of history"):
        detector().score(panel, no_flags(panel), 1000, 1168, no_exclusions(panel))


def test_calendar_flags_explain_planned_events() -> None:
    events = [(400, 424), (500, 524), (600, 624), (1000, 1024), (1400, 1424)]
    panel = synthetic_panel(event_hours=events)
    flagged = no_flags(panel)
    for lo, hi in events:
        flagged["planned_promotion"][:, lo:hi] = True
    window = slice(1400 - START, 1424 - START)
    actual = panel.value[:, 1400:1424]

    informed = detector().score(panel, flagged, START, STOP, no_exclusions(panel))
    blind = detector().score(panel, no_flags(panel), START, STOP, no_exclusions(panel))
    informed_error = float(np.mean(np.abs(actual - informed.expected[:, window])))
    blind_error = float(np.mean(np.abs(actual - blind.expected[:, window])))
    assert informed_error < 0.5 * blind_error


def test_zero_volume_is_unscored_without_warnings() -> None:
    panel = synthetic_panel()
    num, den = panel.num.copy(), panel.den.copy()
    num[3, 1350], den[3, 1350] = 0.0, 0.0
    quiet = Panel(panel.metric, panel.slices, num, den)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        scores = detector().score(quiet, no_flags(quiet), START, STOP, no_exclusions(quiet))
    assert np.isnan(scores.pvalue[3, 1350 - START])
    assert np.isfinite(scores.pvalue[3, 1351 - START])


def test_buckets_are_merged_when_calibration_is_thin() -> None:
    assert bucket_count(60_000, 706) == 3
    assert bucket_count(30_000, 706) == 2
    assert bucket_count(20_000, 706) == 1
    assert bucket_count(100, 706) == 1


def test_a_small_slice_is_judged_against_its_own_noise() -> None:
    # One calibration table for every slice: large slices must not set the bar for small ones.
    # Hours 2 to 11 of the day, where the daily shape keeps the slice's volume steady.
    panel = with_factor(mixed_volume_counts(), 0, 1370, 1380, 1.6)
    single_table = GbmDetector(n_tests=10_000)
    scores = single_table.score(panel, no_flags(panel), START, STOP, no_exclusions(panel))
    window = slice(1370 - START, 1380 - START)
    # A slice of a few hundred sessions has the odd noisy hour; persistence needs two of three.
    assert float((scores.pvalue[0, window] < 0.001).mean()) >= 0.8
    assert float(np.median(scores.pvalue[0, window])) < 1e-4
    assert (scores.strength[0, window] > 1.0).all()
    quiet = scores.pvalue[np.isfinite(scores.pvalue)]
    assert 0.02 < float((quiet < 0.05).mean()) < 0.09


def test_planned_event_hours_are_not_calibration_data() -> None:
    # A promotion the model has never seen sits in the calibration window, flagged in the calendar.
    panel = with_factor(synthetic_panel(event_hours=[(1000, 1024)]), 5, 1400, 1410, 0.75)
    flagged = no_flags(panel)
    flagged["planned_promotion"][:, 1000:1024] = True
    scores = detector().score(panel, flagged, START, STOP, no_exclusions(panel))
    assert (scores.pvalue[5, 1400 - START : 1410 - START] < 0.001).all()


# Promotions whose size the model cannot know in advance: it learns +60% from the first
# three, then the next two are +90%.
UNEVEN_EVENTS = [
    (400, 424, 1.6),
    (500, 524, 1.6),
    (600, 624, 1.6),
    (1000, 1024, 1.9),
    (1400, 1424, 1.9),
]


def flags_for(panel: Panel, events: list[tuple[int, int, float]]) -> dict[str, np.ndarray]:
    flagged = no_flags(panel)
    for lo, hi, _ in events:
        flagged["planned_promotion"][:, lo:hi] = True
    return flagged


def test_planned_event_hours_are_judged_against_earlier_planned_events() -> None:
    panel = synthetic_panel(event_hours=UNEVEN_EVENTS)
    scores = detector().score(
        panel, flags_for(panel, UNEVEN_EVENTS), START, STOP, no_exclusions(panel)
    )
    during = scores.pvalue[:, 1400 - START : 1424 - START]
    # The forecast is off by as much as it was in the last promotion: not an anomaly.
    assert float((during < 0.01).mean()) < 0.1


# The last two promotions are +75% where the model learned +60%: a forecast error of about
# 0.0075 in the rate. The incident below is a drop of about 0.045, six times that.
MILD_EVENTS = [
    (400, 424, 1.6),
    (500, 524, 1.6),
    (600, 624, 1.6),
    (1000, 1024, 1.75),
    (1400, 1424, 1.75),
]


def test_an_incident_inside_a_planned_event_still_stands_out() -> None:
    panel = with_factor(synthetic_panel(event_hours=MILD_EVENTS), 5, 1405, 1415, 0.4)
    scores = detector().score(
        panel, flags_for(panel, MILD_EVENTS), START, STOP, no_exclusions(panel)
    )
    # Small enough to pass Benjamini-Hochberg alone among 706 slices.
    assert (scores.pvalue[5, 1405 - START : 1415 - START] < 0.05 / 706).all()
    assert (scores.strength[5, 1405 - START : 1415 - START] < -1.0).all()


def test_one_leftover_anomaly_does_not_mask_the_calibration_table() -> None:
    # An unexplained spike elsewhere sits in the calibration window; nobody confirmed it.
    panel = with_factor(with_factor(synthetic_panel(), 60, 1000, 1003, 6.0), 5, 1400, 1410, 0.4)
    scores = detector().score(panel, no_flags(panel), START, STOP, no_exclusions(panel))
    assert (scores.pvalue[5, 1400 - START : 1410 - START] < 0.05 / 706).all()


def test_scores_do_not_depend_on_later_data_with_flags_and_exclusions() -> None:
    base = synthetic_panel(event_hours=UNEVEN_EVENTS)
    changed = with_factor(base, slice(None), 1400, base.num.shape[1], 3.0)
    exclude = no_exclusions(base)
    exclude[7, 900:910] = True
    a = detector().score(base, flags_for(base, UNEVEN_EVENTS), START, 1400, exclude)
    b = detector().score(changed, flags_for(changed, UNEVEN_EVENTS), START, 1400, exclude)
    np.testing.assert_array_equal(a.pvalue, b.pvalue)
    np.testing.assert_array_equal(a.strength, b.strength)


def test_finds_a_shift_too_small_for_one_hour_over_several() -> None:
    panel = with_factor(synthetic_panel(), 5, 1400, 1412, 0.92)
    late = slice(1404 - START, 1412 - START)
    scores = detector().score(panel, no_flags(panel), START, STOP, no_exclusions(panel))
    assert float(np.median(scores.pvalue[5, late])) < 1e-4
    assert (scores.strength[5, late] < -1.0).all()
    hourly = GbmDetector(n_tests=90, windows=(1,)).score(
        panel, no_flags(panel), START, STOP, no_exclusions(panel)
    )
    assert float(np.median(hourly.pvalue[5, late])) > 1e-3


def test_the_four_hour_test_has_to_hold_on_two_windows_in_a_row() -> None:
    # One bad hour sits in four windows. The first of them is not yet a pass. (No cap on
    # the hour here, so that one hour is enough to carry a window.)
    panel = with_factor(synthetic_panel(), 5, 1400, 1401, 0.5)
    four = GbmDetector(n_tests=90, windows=(4,), hour_cap=np.inf).score(
        panel, no_flags(panel), START, STOP, no_exclusions(panel)
    )
    first = 1400 - START
    assert four.pvalue[5, first] > 0.01
    assert (four.pvalue[5, first + 1 : first + 4] < 1e-3).all()
    assert four.pvalue[5, first + 4] > 0.01


def test_the_first_hour_of_a_fold_is_held_against_the_hour_before_it() -> None:
    quiet_before = with_factor(synthetic_panel(), 5, START, START + 1, 0.5)
    bad_before = with_factor(synthetic_panel(), 5, START - 1, START, 0.5)
    for panel, held in ((quiet_before, False), (bad_before, True)):
        four = GbmDetector(n_tests=90, windows=(4,), hour_cap=np.inf).score(
            panel, no_flags(panel), START, STOP, no_exclusions(panel)
        )
        assert (four.pvalue[5, 0] < 1e-3) == held


def test_the_hours_after_a_planned_event_are_not_judged_against_its_level() -> None:
    # A day and a week after a promotion, the same hour one day and one week earlier
    # was a promotion hour. Read as an ordinary lag, it says "expect far more".
    panel = synthetic_panel(event_hours=UNEVEN_EVENTS)
    scores = detector().score(
        panel, flags_for(panel, UNEVEN_EVENTS), START, STOP, no_exclusions(panel)
    )
    next_day = scores.pvalue[:, 1424 - START : 1448 - START]
    assert float((next_day < 0.01).mean()) < 0.05


def test_one_extreme_hour_does_not_carry_the_four_hour_test() -> None:
    # The hourly test sees it, and persistence then asks for a second hour. Summed
    # uncapped, the same hour would pass on all four windows that contain it.
    panel = with_factor(synthetic_panel(), 5, 1400, 1401, 0.3)
    first = 1400 - START
    four = GbmDetector(n_tests=90, windows=(4,)).score(
        panel, no_flags(panel), START, STOP, no_exclusions(panel)
    )
    assert (four.pvalue[5, first : first + 4] > 1e-3).all()
    both = detector().score(panel, no_flags(panel), START, STOP, no_exclusions(panel))
    assert both.pvalue[5, first] < 1e-6
    assert (both.pvalue[5, first + 1 : first + 4] > 1e-3).all()


def test_windows_that_reach_back_into_a_planned_event_are_judged_as_event_hours() -> None:
    # The three hours after a promotion are ordinary hours, but their four-hour windows
    # are mostly promotion, with a forecast that is off by as much as it was last time.
    panel = synthetic_panel(event_hours=UNEVEN_EVENTS)
    four = GbmDetector(n_tests=90, windows=(4,)).score(
        panel, flags_for(panel, UNEVEN_EVENTS), START, STOP, no_exclusions(panel)
    )
    tail = four.pvalue[:, 1424 - START : 1427 - START]
    assert float((tail < 0.05).mean()) < 0.1  # a quarter of them, ranked as quiet hours


def test_scores_inside_a_fold_do_not_depend_on_later_hours_of_the_fold() -> None:
    base = synthetic_panel(event_hours=MILD_EVENTS)
    changed = with_factor(base, slice(None), 1430, base.num.shape[1], 3.0)
    confirmed = no_exclusions(base)
    confirmed[7, 900:910] = True
    flags = flags_for(base, MILD_EVENTS)
    a = detector().score(base, flags, START, STOP, confirmed)
    b = detector().score(changed, flags, START, STOP, confirmed)
    before = slice(0, 1430 - START)
    np.testing.assert_array_equal(a.pvalue[:, before], b.pvalue[:, before])
    np.testing.assert_array_equal(a.expected[:, before], b.expected[:, before])
    np.testing.assert_array_equal(a.strength[:, before], b.strength[:, before])


def test_confirmed_incident_hours_are_neither_training_nor_calibration_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Half the slices lose half their conversion for four days of the calibration window.
    panel = with_factor(
        with_factor(synthetic_panel(), slice(0, 45), 900, 996, 0.5), 60, 1400, 1410, 0.8
    )
    confirmed = no_exclusions(panel)
    confirmed[:45, 900:996] = True
    window = slice(1400 - START, 1410 - START)
    blind = detector().score(panel, no_flags(panel), START, STOP, no_exclusions(panel))
    aware = detector().score(panel, no_flags(panel), START, STOP, confirmed)
    # Left in, the incident widens every table and hides a 20% drop in another slice.
    assert (
        float(np.median(aware.pvalue[60, window])) < float(np.median(blind.pvalue[60, window])) / 10
    )

    rows: list[int] = []
    fit = HistGradientBoostingRegressor.fit

    def counting(self: HistGradientBoostingRegressor, x: np.ndarray, y: np.ndarray) -> object:
        rows.append(len(y))
        return fit(self, x, y)

    monkeypatch.setattr(HistGradientBoostingRegressor, "fit", counting)
    small = GbmDetector(n_tests=90, max_train_rows=10**9)
    early = no_exclusions(panel)
    early[:45, 400:496] = True  # inside the training window
    small.score(panel, no_flags(panel), START, STOP, no_exclusions(panel))
    small.score(panel, no_flags(panel), START, STOP, early)
    assert rows[0] - rows[1] == 45 * 96
