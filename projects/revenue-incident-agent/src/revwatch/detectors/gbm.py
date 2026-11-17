"""Gradient-boosted forecaster, scored with split-conformal p-values.

One model per metric across all its slices predicts the expected value of each
slice-hour from calendar and lag features. The residual, divided by a
volume-dependent scale and by the slice's own spread, is ranked against the
residuals of a calibration window that the model was not trained on. That rank
is the p-value, so it holds whatever shape the residuals have.

Two things keep the ranking sharp:

- The spread of each slice is measured on the last weeks of the training
  window. Without it, slices that are noisier than the rough scale assumes (large
  traffic slices) set the bar for all the others in the same calibration table.
- Planned-event hours and normal hours are ranked separately (Mondrian
  conformal). A planned hour is not noise: left in the normal table, a few
  hundred large residuals from one newsletter hide a real incident behind them,
  and judged against the normal table, a promotion the model has seen twice
  looks like an anomaly in every slice. Planned hours are ranked against earlier
  planned hours instead. Most of those were in the training data, so that table
  is somewhat optimistic; with fewer than 200 scores it is not used at all.

Each slice-hour is tested twice: on the hour itself, and on the hour together
with the three before it. The second test has its own spread per slice and its
own calibration tables, built from the same windows, so it stays a rank against
what four quiet hours in a row look like. Two rules keep it to changes that
last: one hour counts for at most `HOUR_CAP` spreads in the sum, and a window
counts only if the window before it passed as well (see `held`).
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits

from revwatch.detectors.base import (
    WINDOWS,
    Scores,
    held,
    lagged,
    most_significant,
    noise_scale,
    reaching,
    robust_spread,
    to_value,
    transform,
    windowed,
)
from revwatch.models import DIMENSIONS, BoolArray, FloatArray
from revwatch.warehouse import CALENDAR_KINDS, Panel

MAX_BUCKETS = 3
SCORES_PER_TEST = 20  # a bucket needs this many calibration scores per simultaneous test
TAIL_FROM = 0.99  # beyond this quantile of a table, p-values come from a fitted tail
# In a test over several hours, one hour counts for at most this many of its slice's
# hourly spreads. Such a test is for changes that last. Summed as it is, one badly
# forecast hour passes on every window that contains it, which is exactly the lone
# hour that persistence exists to ignore.
HOUR_CAP = 4.0


def bucket_count(n_scores: int, n_tests: int) -> int:
    """How many volume buckets the calibration scores can be split into."""
    return int(np.clip(n_scores // (SCORES_PER_TEST * n_tests), 1, MAX_BUCKETS))


def _rank(
    score: FloatArray, cells: BoolArray, table: FloatArray, pvalue: FloatArray, level: FloatArray
) -> None:
    """Write p-values for `cells` in place: the rank of each score in the sorted `table`.

    Up to the table's 99th percentile the p-value is the conformal rank,
    (1 + scores at least as large) / (n + 1). Beyond it the rank is replaced by an
    exponential tail through the 95th and 99th percentiles. Two reasons:

    - A rank cannot be smaller than 1 / (n + 1). With a few thousand scores that
      is above what Benjamini-Hochberg needs to flag one slice among 700, so a
      lone slice could never alarm, however extreme.
    - A rank is at the mercy of the largest scores in the table. One anomaly that
      nobody confirmed would hide every smaller one for as long as it stayed in
      the calibration window. The two percentiles do not move when it arrives.

    The price is that p-values under 0.01 rest on the shape of the tail and no
    longer on ranks alone. An exponential tail is heavier than a normal one, so
    for well-behaved residuals it errs towards larger p-values.

    `level` receives the 95th percentile, the score at the edge of the expected band.
    """
    if table.size == 0 or not cells.any():
        return
    values = score[cells]
    at_least = table.size - np.searchsorted(table, values, side="left")
    p = (1 + at_least) / (table.size + 1)
    q95, q99 = np.quantile(table, [0.95, TAIL_FROM])
    rate = (q99 - q95) / np.log((1 - 0.95) / (1 - TAIL_FROM))
    beyond = values > q99
    if rate > 0 and beyond.any():
        p[beyond] = (1 - TAIL_FROM) * np.exp(-(values[beyond] - q99) / rate)
    pvalue[cells] = p
    level[cells] = q95


@dataclass(frozen=True)
class _Residuals:
    expected: FloatArray  # metric units
    unit: FloatArray  # residual on the modelling scale / noise scale, signed
    volume: FloatArray  # expected count, or the rate's denominator


@dataclass(frozen=True)
class _Event:
    """One planned event before the fold, from a few hours before it to a few hours after."""

    unit: FloatArray  # residuals, NaN where excluded
    planned: BoolArray  # the hours planned for each slice
    enough: BoolArray  # the hours above the volume floor


class GbmDetector:
    name = "gbm"
    LAGS = (24, 168, 336)  # no lag under 24 hours: the forecast must not follow an incident
    ROLLING_WEEKS = 4
    MIN_TRAIN_HOURS = 168
    CALIBRATION_HOURS = 672
    SPREAD_HOURS = 336  # the end of the training window, used to measure each slice's spread
    HISTORY_NEEDED = max(LAGS) + MIN_TRAIN_HOURS + CALIBRATION_HOURS
    MIN_TRAIN_ROWS = 500
    MIN_CALIBRATION_SCORES = 100
    MIN_PLANNED_SCORES = 200  # below this, planned-event hours use the normal tables

    def __init__(
        self,
        seed: int = 7,
        max_train_rows: int = 120_000,
        n_tests: int = 706,
        max_iter: int = 150,
        threads: int = 4,
        windows: Sequence[int] = WINDOWS,
        hour_cap: float = HOUR_CAP,
    ) -> None:
        # Measured on the dev world: with every core, the many small fits spend most of
        # their time contending for threads (about 1,300 CPU-seconds); with four threads
        # the same evaluation takes about 260 and half the wall time. Results are identical.
        self.threads = threads
        self.seed = seed
        self.max_train_rows = max_train_rows
        self.n_tests = n_tests
        self.max_iter = max_iter
        self.windows = tuple(windows)
        self.hour_cap = hour_cap

    def score(
        self,
        panel: Panel,
        flags: Mapping[str, BoolArray],
        start: int,
        stop: int,
        exclude: BoolArray,
    ) -> Scores:
        if start < self.HISTORY_NEEDED:
            raise ValueError(
                f"gbm needs {self.HISTORY_NEEDED} hours of history before the first "
                f"scored tick, got {start}"
            )
        metric = panel.metric
        first = max(self.LAGS)
        cal_lo = start - self.CALIBRATION_HOURS
        # Three windows in a row: spread (the end of the training data), calibration, new.
        lo = max(first, cal_lo - self.SPREAD_HOURS)
        i_cal, i_new = cal_lo - lo, start - lo
        y = transform(panel)
        history = np.where(exclude, np.nan, y)
        planned = np.zeros(panel.num.shape, dtype=bool)
        for kind in CALENDAR_KINDS:
            planned |= flags[kind]

        model = self._fit(panel, flags, y, history, exclude, first, cal_lo, start)
        res = self._residuals(model, panel, flags, y, history, lo, stop)
        unit = np.where(exclude[:, lo:stop], np.nan, res.unit)
        events = self._events(model, panel, flags, y, history, exclude, planned, first, start)
        cal_volume = res.volume[:, i_cal:i_new]

        pvalues: list[FloatArray] = []
        strengths: list[FloatArray] = []
        limit = self.hour_cap * robust_spread(unit[:, :i_cal])[:, None]

        def summed(values: FloatArray, window: int) -> FloatArray:
            """`windowed`, with each hour capped when more than one hour is added up."""
            return windowed(values if window == 1 else np.clip(values, -limit, limit), window)

        for window in self.windows:
            combined = summed(unit, window)
            # An hour whose window reaches back into a planned event is judged as an event
            # hour: most of what it adds up is the event.
            touched = reaching(planned, window)
            normal = ~touched[:, cal_lo:start] & (cal_volume >= metric.min_volume)
            # Fixed before any calibration or new hour is looked at, so both stay comparable.
            spread = robust_spread(combined[:, :i_cal])[:, None]
            score = np.abs(combined) / spread
            # A test over several hours is also ranked for the hour before the fold, so
            # that the fold's first hour has a window before it to be held against.
            lead = 1 if window > 1 else 0
            cal, new = score[:, i_cal:i_new], score[:, i_new - lead :]
            new_volume = res.volume[:, i_new - lead :]
            usable = np.isfinite(cal) & normal
            n_usable = int(usable.sum())
            if n_usable < self.MIN_CALIBRATION_SCORES:
                raise ValueError(
                    f"gbm: only {n_usable} usable calibration scores for {metric.name} "
                    f"before tick {start}"
                )
            n_buckets = bucket_count(n_usable, self.n_tests)
            edges = (
                np.quantile(cal_volume[usable], [i / n_buckets for i in range(1, n_buckets)])
                if n_buckets > 1
                else np.empty(0)
            )
            cal_bucket = np.searchsorted(edges, cal_volume[usable], side="right")
            pvalue = np.full(new.shape, np.nan)
            level = np.full(new.shape, np.nan)
            scored = np.isfinite(new) & np.isfinite(new_volume)
            bucket = np.searchsorted(edges, np.where(scored, new_volume, 0.0), side="right")
            for b in range(n_buckets):
                table = np.sort(cal[usable][cal_bucket == b])
                _rank(new, scored & (bucket == b), table, pvalue, level)

            # Planned-event hours get their own table: the forecast is less certain there.
            event_table = np.sort(
                np.concatenate(
                    [
                        (np.abs(summed(e.unit, window)) / spread)[
                            reaching(e.planned, window) & e.enough
                        ]
                        for e in events
                    ]
                    or [np.empty(0)]
                )
            )
            event_table = event_table[np.isfinite(event_table)]
            if event_table.size >= self.MIN_PLANNED_SCORES:
                _rank(new, scored & touched[:, start - lead : stop], event_table, pvalue, level)
            pvalues.append(held(pvalue) if lead else pvalue)
            strengths.append(np.sign(combined[:, i_new:]) * (new / level)[:, lead:])

        pvalue, strength = most_significant(pvalues, strengths)
        return Scores(expected=res.expected[:, i_new:], pvalue=pvalue, strength=strength)

    def _fit(
        self,
        panel: Panel,
        flags: Mapping[str, BoolArray],
        y: FloatArray,
        history: FloatArray,
        exclude: BoolArray,
        lo: int,
        hi: int,
        start: int,
    ) -> HistGradientBoostingRegressor:
        features = self._features(panel, flags, history, lo, hi)
        target = y[:, lo:hi].reshape(-1)
        rows = np.nonzero(np.isfinite(target) & ~exclude[:, lo:hi].reshape(-1))[0]
        if rows.size < self.MIN_TRAIN_ROWS:
            raise ValueError(
                f"gbm: only {rows.size} training rows for {panel.metric.name} before tick {start}"
            )
        if rows.size > self.max_train_rows:
            rng = np.random.default_rng(self.seed + start)
            rows = np.sort(rng.choice(rows, self.max_train_rows, replace=False))
        n_dims = len(panel.metric.dims)
        model = HistGradientBoostingRegressor(
            max_iter=self.max_iter,
            learning_rate=0.08,
            max_leaf_nodes=31,
            min_samples_leaf=50,
            categorical_features=list(range(3, 3 + n_dims)),
            early_stopping=False,
            random_state=self.seed,
        )
        with threadpool_limits(limits=self.threads, user_api="openmp"):
            model.fit(features[rows], target[rows])
        return model

    def _features(
        self,
        panel: Panel,
        flags: Mapping[str, BoolArray],
        history: FloatArray,
        lo: int,
        hi: int,
    ) -> FloatArray:
        """One row per slice-hour in [lo, hi), slices in the outer loop.

        Columns: hour of week, hour of day, day of week, one code per metric
        dimension, the calendar flags, the lags, the rolling median.
        """
        n_slices, n = len(panel.slices), hi - lo
        ticks = np.arange(lo, hi)
        columns: list[FloatArray] = [
            np.broadcast_to((ticks % 168).astype(np.float64), (n_slices, n)),
            np.broadcast_to((ticks % 24).astype(np.float64), (n_slices, n)),
            np.broadcast_to(((ticks // 24) % 7).astype(np.float64), (n_slices, n)),
        ]
        for dim in panel.metric.dims:
            values = DIMENSIONS[dim]
            codes = np.array(
                [
                    values.index(s.as_dict()[dim]) if dim in s.as_dict() else len(values)
                    for s in panel.slices
                ],
                dtype=np.float64,
            )
            columns.append(np.broadcast_to(codes[:, None], (n_slices, n)))
        for kind in CALENDAR_KINDS:
            columns.append(flags[kind][:, lo:hi].astype(np.float64))
        for lag in self.LAGS:
            columns.append(lagged(history, lo, hi, lag))
        weekly = np.stack(
            [lagged(history, lo, hi, 168 * k) for k in range(1, self.ROLLING_WEEKS + 1)]
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # cells with no history yet
            columns.append(np.nanmedian(weekly, axis=0))
        return np.stack([column.reshape(-1) for column in columns], axis=1)

    def _residuals(
        self,
        model: HistGradientBoostingRegressor,
        panel: Panel,
        flags: Mapping[str, BoolArray],
        y: FloatArray,
        history: FloatArray,
        lo: int,
        hi: int,
    ) -> _Residuals:
        features = self._features(panel, flags, history, lo, hi)
        with threadpool_limits(limits=self.threads, user_api="openmp"):
            predicted = model.predict(features)
        y_hat = np.asarray(predicted, dtype=np.float64).reshape(len(panel.slices), hi - lo)
        den = panel.den[:, lo:hi]
        expected = to_value(panel.metric, y_hat)
        volume = expected if panel.metric.kind == "count" else den
        unit = (y[:, lo:hi] - y_hat) / noise_scale(panel.metric, y_hat, den)
        return _Residuals(expected=expected, unit=unit, volume=volume)

    def _events(
        self,
        model: HistGradientBoostingRegressor,
        panel: Panel,
        flags: Mapping[str, BoolArray],
        y: FloatArray,
        history: FloatArray,
        exclude: BoolArray,
        planned: BoolArray,
        first: int,
        start: int,
    ) -> list[_Event]:
        """Residuals of every planned event before `start`.

        Each event is scored with the hours the longest window reaches: that many
        before it, so its first hours have a full window, and that many after it,
        because those windows are still mostly event.
        """
        lead = max(self.windows) - 1
        anywhere = planned[:, first:start].any(axis=0)[None, :]
        columns = np.nonzero(reaching(anywhere, lead + 1)[0])[0] + first
        if columns.size == 0:
            return []
        found: list[_Event] = []
        # Events are runs of consecutive hours; score each run in one call.
        for run in np.split(columns, np.nonzero(np.diff(columns) > 1)[0] + 1):
            lo, hi = int(run[0]), int(run[-1]) + 1
            before = max(first, lo - lead)
            res = self._residuals(model, panel, flags, y, history, before, hi)
            found.append(
                _Event(
                    unit=np.where(exclude[:, before:hi], np.nan, res.unit),
                    planned=planned[:, before:hi],
                    enough=res.volume >= panel.metric.min_volume,
                )
            )
        return found
