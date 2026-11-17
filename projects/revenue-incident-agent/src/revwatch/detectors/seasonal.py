"""Seasonal robust z-score.

The expected value is the median of the same hour of week over the trailing six
weeks. The spread is estimated per slice from the MAD of its last four weeks of
seasonal residuals, after dividing each residual by a volume-dependent scale.

Six reference points are enough for a median but not for a spread: a MAD of six
values is often far too small, and a fixed noise formula is wrong for traffic
that is noisier than Poisson and for rates that are steadier than their volume
suggests. Four weeks of residuals per slice give a stable estimate of both.

The test is on the hour alone. Adding the four-hour test the forecaster uses was
tried and dropped: under a normal assumption its tail is far too thin for
payment success (on quiet weeks of the dev world 0.8% to 1.7% of slice-hours had
p < 0.0001 where 0.01% are expected), because a slice that runs low for a few
days makes four residuals in a row agree. The forecaster ranks against what
actually happened in its calibration weeks, so it does not assume a shape.
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping

import numpy as np
from scipy.special import erfc

from revwatch.detectors.base import (
    Scores,
    lagged,
    noise_scale,
    robust_spread,
    to_value,
    transform,
)
from revwatch.models import BoolArray, FloatArray
from revwatch.warehouse import Panel


class SeasonalDetector:
    name = "seasonal"
    windows: tuple[int, ...] = (1,)
    WEEKS = 6
    MIN_REFERENCES = 4
    SPREAD_HOURS = 672  # residuals from the four weeks before the fold set the spread

    def score(
        self,
        panel: Panel,
        flags: Mapping[str, BoolArray],
        start: int,
        stop: int,
        exclude: BoolArray,
    ) -> Scores:
        y = transform(panel)
        history = np.where(exclude, np.nan, y)
        lo = max(0, start - self.SPREAD_HOURS)
        median, count = self._reference(history, lo, stop)
        # Residuals in units of the rough noise scale, from the spread window to the fold's end.
        unit = (y[:, lo:stop] - median) / noise_scale(panel.metric, median, panel.den[:, lo:stop])
        unit[count < self.MIN_REFERENCES] = np.nan
        past = start - lo
        spread = robust_spread(np.where(exclude[:, lo:start], np.nan, unit[:, :past]))
        z = unit[:, past:] / spread[:, None]
        pvalue = np.asarray(erfc(np.abs(z) / np.sqrt(2.0)), dtype=np.float64)
        return Scores(
            expected=to_value(panel.metric, median[:, past:]), pvalue=pvalue, strength=z / 1.96
        )

    def _reference(self, history: FloatArray, lo: int, hi: int) -> tuple[FloatArray, FloatArray]:
        """Median of the same hour of week over the trailing weeks, and how many values it used."""
        refs = np.stack([lagged(history, lo, hi, 168 * k) for k in range(1, self.WEEKS + 1)])
        count = np.isfinite(refs).sum(axis=0).astype(np.float64)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # columns with no reference at all
            median: FloatArray = np.nanmedian(refs, axis=0)
        return median, count
