import warnings

import numpy as np
import pytest

from revwatch.detectors.base import (
    Detector,
    held,
    lagged,
    most_significant,
    noise_scale,
    reaching,
    windowed,
)
from revwatch.detectors.seasonal import SeasonalDetector
from revwatch.detectors.static import StaticDetector
from revwatch.warehouse import METRICS, Panel
from tests.helpers import (
    no_exclusions,
    no_flags,
    overdispersed_counts,
    quiet_rate,
    synthetic_panel,
    with_factor,
)

START, STOP = 1344, 1512
DETECTORS: list[Detector] = [StaticDetector(), SeasonalDetector()]
by_name = pytest.mark.parametrize("detector", DETECTORS, ids=[d.name for d in DETECTORS])


def test_lagged_pads_with_nan_before_the_first_column() -> None:
    values = np.arange(10, dtype=np.float64).reshape(1, 10)
    assert lagged(values, 4, 7, 3).tolist() == [[1.0, 2.0, 3.0]]
    out = lagged(values, 1, 4, 3)
    assert np.isnan(out[0, :2]).all() and out[0, 2] == 0.0
    assert np.isnan(lagged(values, 0, 2, 5)).all()


def test_noise_scale_shrinks_with_volume() -> None:
    metric = METRICS["checkout_conversion"]
    small = noise_scale(metric, np.array([0.05]), np.array([100.0]))
    large = noise_scale(metric, np.array([0.05]), np.array([10_000.0]))
    assert small[0] == pytest.approx(10 * large[0])
    assert noise_scale(metric, np.array([0.05]), np.array([0.0]))[0] > 0


@by_name
def test_flags_an_injected_drop(detector: Detector) -> None:
    panel = with_factor(synthetic_panel(), 5, 1400, 1410, 0.4)
    scores = detector.score(panel, no_flags(panel), START, STOP, no_exclusions(panel))
    window = slice(1400 - START, 1410 - START)
    assert scores.pvalue.shape == (90, STOP - START)
    assert (scores.pvalue[5, window] < 0.01).all()
    assert (scores.strength[5, window] < -1.0).all()
    assert 0.03 < float(np.mean(scores.expected[5, window])) < 0.08


@by_name
def test_scores_do_not_depend_on_later_data(detector: Detector) -> None:
    base = synthetic_panel()
    changed = with_factor(base, slice(None), 1400, base.num.shape[1], 3.0)
    a = detector.score(base, no_flags(base), START, 1400, no_exclusions(base))
    b = detector.score(changed, no_flags(changed), START, 1400, no_exclusions(changed))
    np.testing.assert_array_equal(a.pvalue, b.pvalue)
    np.testing.assert_array_equal(a.expected, b.expected)
    np.testing.assert_array_equal(a.strength, b.strength)


@by_name
def test_zero_volume_is_unscored_without_warnings(detector: Detector) -> None:
    panel = synthetic_panel()
    num, den = panel.num.copy(), panel.den.copy()
    num[3, 1350], den[3, 1350] = 0.0, 0.0
    quiet = Panel(panel.metric, panel.slices, num, den)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        scores = detector.score(quiet, no_flags(quiet), START, STOP, no_exclusions(quiet))
    assert np.isnan(scores.pvalue[3, 1350 - START])
    assert np.isfinite(scores.pvalue[3, 1351 - START])


def test_static_does_not_score_without_a_reference() -> None:
    panel = synthetic_panel()
    num, den = panel.num.copy(), panel.den.copy()
    num[:, 1400 - 168], den[:, 1400 - 168] = np.nan, np.nan
    gap = Panel(panel.metric, panel.slices, num, den)
    scores = StaticDetector().score(gap, no_flags(gap), START, STOP, no_exclusions(gap))
    assert np.isnan(scores.pvalue[:, 1400 - START]).all()
    assert np.isfinite(scores.pvalue[:, 1401 - START]).all()


def test_seasonal_skips_missing_reference_hours() -> None:
    panel = synthetic_panel()
    num, den = panel.num.copy(), panel.den.copy()
    for weeks in (1, 2):
        num[:, 1400 - 168 * weeks], den[:, 1400 - 168 * weeks] = np.nan, np.nan
    gap = Panel(panel.metric, panel.slices, num, den)
    scores = SeasonalDetector().score(gap, no_flags(gap), START, STOP, no_exclusions(gap))
    column = 1400 - START
    assert np.isfinite(scores.pvalue[:, column]).all()  # four references are left
    assert 0.03 < scores.expected[0, column] < 0.08  # not dragged towards zero

    num[:, 1400 - 168 * 3], den[:, 1400 - 168 * 3] = np.nan, np.nan
    gap = Panel(panel.metric, panel.slices, num, den)
    scores = SeasonalDetector().score(gap, no_flags(gap), START, STOP, no_exclusions(gap))
    assert np.isnan(scores.pvalue[:, column]).all()  # three left: unscored


def test_seasonal_leaves_out_excluded_reference_hours() -> None:
    panel = synthetic_panel()
    exclude = no_exclusions(panel)
    for weeks in (1, 2, 3):
        exclude[5, 1400 - 168 * weeks] = True
    scores = SeasonalDetector().score(panel, no_flags(panel), START, STOP, exclude)
    column = 1400 - START
    assert np.isnan(scores.pvalue[5, column])
    assert np.isfinite(scores.pvalue[4, column])


def test_seasonal_is_not_overconfident_on_overdispersed_counts() -> None:
    panel = overdispersed_counts()
    scores = SeasonalDetector().score(panel, no_flags(panel), START, STOP, no_exclusions(panel))
    p = scores.pvalue[np.isfinite(scores.pvalue)]
    assert p.size == 90 * (STOP - START)
    assert float((p < 0.001).mean()) < 0.01
    assert 0.01 < float((p < 0.05).mean()) < 0.12


def test_seasonal_sees_a_shift_in_a_rate_with_little_noise() -> None:
    panel = quiet_rate()
    num = panel.num.copy()
    num[7, 1400:1410] *= 1.5  # 6% -> 9%
    shifted = Panel(panel.metric, panel.slices, num, panel.den)
    scores = SeasonalDetector().score(
        shifted, no_flags(shifted), START, STOP, no_exclusions(shifted)
    )
    window = slice(1400 - START, 1410 - START)
    assert (scores.pvalue[7, window] < 0.01).all()
    assert (scores.strength[7, window] > 1.0).all()
    quiet = scores.pvalue[8]
    assert float((quiet < 0.001).mean()) < 0.03


def test_windowed_combines_each_hour_with_the_ones_before_it() -> None:
    unit = np.array([[1.0, 1.0, 1.0, 1.0, np.nan, 2.0, np.nan, np.nan, 4.0]])
    out = windowed(unit, 4)
    assert np.isnan(out[0, 0])  # one value so far: less than half a window
    assert out[0, 1] == pytest.approx(2.0 / np.sqrt(2))
    assert out[0, 3] == pytest.approx(2.0)  # four values of 1: 4 / sqrt(4)
    assert np.isnan(out[0, 4])  # the hour itself has no value
    assert out[0, 5] == pytest.approx(4.0 / np.sqrt(3))  # 1 + 1 + 2 over three values
    assert out[0, 8] == pytest.approx(6.0 / np.sqrt(2))  # 2 + 4: half the window is enough
    alone = windowed(np.array([[np.nan, np.nan, np.nan, 4.0]]), 4)
    assert np.isnan(alone[0, 3])  # one value in the window: less than half
    np.testing.assert_array_equal(windowed(unit, 1), unit)


def test_a_held_p_value_is_the_larger_of_this_hour_and_the_one_before() -> None:
    p = np.array([[0.5, 1e-6, 1e-8, np.nan, 1e-7, 1e-9, 0.3]])
    out = held(p)
    assert out.shape == (1, 6)  # the first column only serves the second
    np.testing.assert_array_equal(out[0, :2], [0.5, 1e-6])
    assert np.isnan(out[0, 2])  # an hour that was not scored stays unscored
    assert out[0, 3] == 1e-7  # nothing before it to hold against: it stands alone
    np.testing.assert_array_equal(out[0, 4:], [1e-7, 0.3])


def test_static_thresholds_are_tuned_on_the_warm_up_to_fire_rarely() -> None:
    panel = synthetic_panel()
    scores = StaticDetector().score(panel, no_flags(panel), START, STOP, no_exclusions(panel))
    crossed = float((scores.pvalue == 0.0).mean())
    assert 0.0 < crossed < 0.005  # about one slice-hour in a thousand, not one in ten


def test_a_static_rule_set_to_fire_less_often_has_a_higher_threshold() -> None:
    panel = synthetic_panel()
    usual = StaticDetector().score(panel, no_flags(panel), START, STOP, no_exclusions(panel))
    strict = StaticDetector(fire_rate=0.0001).score(
        panel, no_flags(panel), START, STOP, no_exclusions(panel)
    )
    assert 0 < (strict.pvalue == 0.0).sum() < (usual.pvalue == 0.0).sum()


def test_static_thresholds_do_not_move_after_the_warm_up() -> None:
    base = synthetic_panel()
    loud = with_factor(base, slice(0, 45), 1350, 1400, 3.0)  # after the warm-up
    a = StaticDetector().score(base, no_flags(base), 1400, STOP, no_exclusions(base))
    b = StaticDetector().score(loud, no_flags(loud), 1400, STOP, no_exclusions(loud))
    np.testing.assert_array_equal(a.strength[60:, 100:], b.strength[60:, 100:])


def test_static_strength_is_in_units_of_its_band_like_the_other_detectors() -> None:
    # The grouper reads a strength of 1 as the edge of the expected band, whichever
    # detector it came from. The alarm threshold lies well outside that band.
    panel = synthetic_panel()
    scores = StaticDetector().score(panel, no_flags(panel), START, STOP, no_exclusions(panel))
    outside = float((np.abs(scores.strength) > 1.0).mean())
    assert 0.02 < outside < 0.10  # about one slice-hour in twenty
    assert (np.abs(scores.strength[scores.pvalue == 0.0]) > 1.5).all()


def test_static_does_not_compare_with_an_hour_known_to_be_an_incident() -> None:
    panel = with_factor(synthetic_panel(), 5, 1300, 1310, 0.5)
    echo = slice(1468 - START, 1478 - START)  # the same hours one week later
    blind = StaticDetector().score(panel, no_flags(panel), START, STOP, no_exclusions(panel))
    assert (blind.pvalue[5, echo] == 0.0).all()  # a healthy week looks like a jump
    confirmed = no_exclusions(panel)
    confirmed[5, 1300:1310] = True
    scores = StaticDetector().score(panel, no_flags(panel), START, STOP, confirmed)
    assert (scores.pvalue[5, echo] == 1.0).all()
    np.testing.assert_array_equal(scores.expected[5, echo], panel.value[5, 1132:1142])


@by_name
def test_scores_inside_a_fold_do_not_depend_on_later_hours_of_the_fold(detector: Detector) -> None:
    # The replay scores a week at a time and then walks it hour by hour.
    base = synthetic_panel()
    changed = with_factor(base, slice(None), 1400, base.num.shape[1], 3.0)
    a = detector.score(base, no_flags(base), START, STOP, no_exclusions(base))
    b = detector.score(changed, no_flags(changed), START, STOP, no_exclusions(changed))
    before = slice(0, 1400 - START)
    np.testing.assert_array_equal(a.pvalue[:, before], b.pvalue[:, before])
    np.testing.assert_array_equal(a.expected[:, before], b.expected[:, before])
    np.testing.assert_array_equal(a.strength[:, before], b.strength[:, before])


def test_the_smallest_of_several_p_values_pays_for_the_number_of_tests() -> None:
    hourly = np.array([[1e-4, 0.6, np.nan, 0.9]])
    four = np.array([[1e-2, 1e-3, 1e-5, 0.8]])
    pvalue, strength = most_significant(
        [hourly, four], [np.full((1, 4), 1.0), np.full((1, 4), -4.0)]
    )
    np.testing.assert_allclose(pvalue[0], [2e-4, 2e-3, 2e-5, 1.0])
    assert strength[0].tolist() == [1.0, -4.0, -4.0, -4.0]  # from the test that won


def test_reaching_marks_the_hours_whose_window_touches_a_flag() -> None:
    flags = np.array([[False, True, False, False, False, False, True]])
    assert reaching(flags, 1).tolist() == flags.tolist()
    assert reaching(flags, 4).tolist() == [[False, True, True, True, True, False, True]]


def test_static_tunes_only_on_slice_hours_it_would_score() -> None:
    # Ten slices far under the volume floor swing wildly from week to week. They are
    # never scored, so they must not set the threshold for the others.
    panel = synthetic_panel()
    rng = np.random.default_rng(1)
    den, num = panel.den.copy(), panel.num.copy()
    den[:10] = 40.0
    num[:10] = rng.binomial(40, 0.05, size=num[:10].shape)
    thin = Panel(panel.metric, panel.slices, num, den)
    scores = StaticDetector().score(thin, no_flags(thin), START, STOP, no_exclusions(thin))
    crossed = float((scores.pvalue[10:] == 0.0).mean())
    assert 0.0003 < crossed < 0.005
