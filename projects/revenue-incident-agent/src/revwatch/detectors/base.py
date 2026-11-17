"""What every detector returns, and the helpers they share."""

from __future__ import annotations

import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from revwatch.models import BoolArray, FloatArray
from revwatch.warehouse import MetricDef, Panel

# Hours each test looks at. The hourly test finds a sharp break within two hours. A
# change worth 3 to 4 standard deviations an hour can sit under the bar that one
# slice among 700 has to clear for as long as it lasts; added up over four hours it
# is 7 to 8 and clears it easily. A test over several hours has to hold on two
# windows in a row (`held`), and the forecaster caps what one hour adds to it.
WINDOWS = (1, 4)


@dataclass(frozen=True)
class Scores:
    """Detector output for columns start..stop-1; each array is (n_slices, stop - start).

    expected  the metric value the detector expected, in the metric's own units
    pvalue    two-sided; NaN where the detector could not score
    strength  signed deviation in units of the detector's own alarm level: the
              sign is the direction and |strength| > 1 is beyond that level
    """

    expected: FloatArray
    pvalue: FloatArray
    strength: FloatArray


class Detector(Protocol):
    name: str
    windows: tuple[int, ...]  # the hours each of its tests adds up; (1,) for an hourly test

    def score(
        self,
        panel: Panel,
        flags: Mapping[str, BoolArray],
        start: int,
        stop: int,
        exclude: BoolArray,
    ) -> Scores:
        """Score columns start..stop-1. Scoring column t may read columns <= t only.

        `exclude[s, t]` marks slice-hours known to be incidents; they must not be
        used as reference or training data.
        """
        ...


def lagged(values: FloatArray, start: int, stop: int, lag: int) -> FloatArray:
    """Columns start-lag..stop-lag-1 of `values`; NaN where that is before column 0."""
    out = np.full((values.shape[0], stop - start), np.nan)
    first = max(0, lag - start)  # leading columns with no history
    if first < stop - start:
        out[:, first:] = values[:, start - lag + first : stop - lag]
    return out


def transform(panel: Panel) -> FloatArray:
    """The scale a detector models: log1p for counts, the rate itself for rates."""
    if panel.metric.kind == "count":
        return np.log1p(panel.num)
    return panel.value


def to_value(metric: MetricDef, y: FloatArray) -> FloatArray:
    """Back from the modelling scale to the metric's own units."""
    return np.expm1(y) if metric.kind == "count" else y


def noise_scale(metric: MetricDef, y_hat: FloatArray, den: FloatArray) -> FloatArray:
    """Rough standard deviation of the modelled value, so residuals compare across volumes.

    It only has to be proportional to the real noise: the seasonal detector uses
    it as a floor and the forecaster calibrates against it.
    """
    if metric.scale == "log_count":
        return 1.0 / np.sqrt(np.maximum(np.expm1(y_hat), 1.0))
    volume = np.maximum(den, 1.0)
    if metric.scale == "binomial":
        return np.sqrt(np.clip(y_hat * (1.0 - y_hat), 1e-4, None) / volume)
    return 1.0 / np.sqrt(volume)


def robust_spread(unit: FloatArray, min_points: int = 50, floor: float = 0.05) -> FloatArray:
    """Per row: how wide the residuals are, in units of the rough noise scale.

    `unit` holds residuals already divided by `noise_scale`, one row per slice;
    NaN entries are ignored. The result is 1.4826 x the median absolute value, so
    it reads as a standard deviation and a few real anomalies do not move it. A
    slice with fewer than `min_points` values borrows the typical spread of the
    others. The floor keeps a perfectly flat slice from dividing by zero.
    """
    enough = np.isfinite(unit).sum(axis=1) >= min_points
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # rows with no values at all
        spread = 1.4826 * np.nanmedian(np.abs(unit), axis=1)
        typical = np.nanmedian(spread[enough]) if enough.any() else np.nan
    return np.maximum(np.where(enough, spread, typical), floor)


def windowed(unit: FloatArray, window: int) -> FloatArray:
    """Each column combined with the `window - 1` columns before it: sum / sqrt(count).

    For independent residuals of unit spread the result has unit spread as well,
    so a shift that lasts grows with the square root of the hours it has lasted.
    NaN entries are left out. The result is NaN where the column itself is NaN (an
    hour that was not observed is not scored) or less than half the window is there.
    """
    if window == 1:
        return unit
    finite = np.isfinite(unit)
    pad = ((0, 0), (window, 0))
    total = np.cumsum(np.pad(np.where(finite, unit, 0.0), pad), axis=1)
    count = np.cumsum(np.pad(finite.astype(np.float64), pad), axis=1)
    total = total[:, window:] - total[:, :-window]
    count = count[:, window:] - count[:, :-window]
    out = np.full(unit.shape, np.nan)
    enough = finite & (count >= (window + 1) // 2)
    np.divide(total, np.sqrt(count), out=out, where=enough)
    return out


def reaching(flags: BoolArray, window: int) -> BoolArray:
    """True where the column, or one of the `window - 1` columns before it, is flagged."""
    if window == 1:
        return flags
    count = np.cumsum(np.pad(flags.astype(np.int64), ((0, 0), (window, 0))), axis=1)
    reached: BoolArray = (count[:, window:] - count[:, :-window]) > 0
    return reached


def held(pvalue: FloatArray) -> FloatArray:
    """The p-value of each column held against the column before: the larger of the two.

    For a test over several hours. Two windows in a row share all but one of
    their hours, so asking for two passes in three ticks, which is what
    persistence does, asks such a test for almost nothing: one noisy stretch
    passes on every window that contains it. A window therefore counts only if
    the window before it passed as well. The result has one column less: the
    first only serves the second. Where the column before was not scored, a
    column stands alone.
    """
    now, before = pvalue[:, 1:], pvalue[:, :-1]
    return np.where(np.isnan(before), now, np.maximum(now, before))


def most_significant(
    pvalues: Sequence[FloatArray], strengths: Sequence[FloatArray]
) -> tuple[FloatArray, FloatArray]:
    """One p-value and strength per cell from several tests of the same cell.

    The smallest p-value wins and is multiplied by the number of tests
    (Bonferroni), so looking at more windows does not raise the false-alarm rate.
    A test that could not score a cell (NaN) is ignored for that cell.
    """
    p = np.stack(pvalues)
    best = np.argmin(np.where(np.isnan(p), np.inf, p), axis=0)[None]
    pvalue = np.minimum(1.0, len(pvalues) * np.take_along_axis(p, best, axis=0)[0])
    strength = np.take_along_axis(np.stack(strengths), best, axis=0)[0]
    return pvalue, strength
