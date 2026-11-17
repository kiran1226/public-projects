"""Baseline: percentage change against the same hour one week earlier."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from revwatch.detectors.base import Scores, lagged
from revwatch.models import BoolArray, FloatArray
from revwatch.policy import DetectionPolicy
from revwatch.warehouse import Panel


class StaticDetector:
    """What a dashboard alert does today: one percentage per metric, for every slice.

    It has no notion of significance, so it reports p = 0 when its threshold is
    crossed and p = 1 otherwise. Its strength is the change in units of the change
    that 95% of warm-up slice-hours stayed under, so that 1 is the edge of the
    expected band, as it is for the other detectors.

    The reference is the same hour one week earlier. Where that hour is known to
    be an incident, the week before it is used: otherwise every incident it
    found would come back a week later as a healthy hour that looks like a jump.

    The thresholds are set once, from the warm-up weeks, the way a team tunes
    them: each is raised until it would have fired on one slice-hour in a
    thousand. That is the best case for a static rule; hand-picked round numbers
    fired constantly. What tuning cannot fix is that one percentage has to fit
    every slice: it is set by the noisiest ones, so the large, steady slices can
    lose a fifth of their volume without crossing it. `fire_rate` moves along
    that trade-off; the measured curve is in the decision log.
    """

    name = "static"
    windows: tuple[int, ...] = (1,)
    BAND = 0.95  # the share of warm-up slice-hours inside the expected band
    MIN_TUNING_CELLS = 1000

    def __init__(
        self, tuned_until: int = DetectionPolicy().warmup_hours, fire_rate: float = 0.001
    ) -> None:
        self.tuned_until = tuned_until
        self.fire_rate = fire_rate  # the share of warm-up slice-hours the rule may fire on

    def score(
        self,
        panel: Panel,
        flags: Mapping[str, BoolArray],
        start: int,
        stop: int,
        exclude: BoolArray,
    ) -> Scores:
        value = panel.value
        band, threshold = self._levels(panel, value, min(start, self.tuned_until))
        history = np.where(exclude, np.nan, value)
        known_incident = lagged(exclude.astype(np.float64), start, stop, 168) == 1.0
        reference = np.where(
            known_incident, lagged(history, start, stop, 336), lagged(value, start, stop, 168)
        )
        change = self._change(value[:, start:stop], reference)
        crossed = np.abs(change) >= threshold
        pvalue = np.where(np.isnan(change), np.nan, np.where(crossed, 0.0, 1.0))
        return Scores(expected=reference, pvalue=pvalue, strength=change / band)

    @staticmethod
    def _change(current: FloatArray, reference: FloatArray) -> FloatArray:
        change = np.full(current.shape, np.nan)
        np.divide(current - reference, reference, out=change, where=reference > 0)
        return change

    def _levels(self, panel: Panel, value: FloatArray, until: int) -> tuple[float, float]:
        """From the hours before `until`: the edge of the band, and the alarm threshold.

        The threshold is the change that the allowed share of slice-hours exceeded.
        """
        if until <= 168:
            raise ValueError(f"static needs more than a week of history, got {until} hours")
        reference = lagged(value, 168, until, 168)
        change = np.abs(self._change(value[:, 168:until], reference))
        # Only slice-hours that are scored at all: the same volume floor as the replay.
        volume = reference if panel.metric.kind == "count" else panel.den[:, 168:until]
        seen = change[np.isfinite(change) & (volume >= panel.metric.min_volume)]
        if seen.size < self.MIN_TUNING_CELLS:
            raise ValueError(
                f"static: only {seen.size} slice-hours to tune {panel.metric.name} on "
                f"before hour {until}"
            )
        band, threshold = np.quantile(seen, [self.BAND, 1.0 - self.fire_rate])
        return float(band), float(threshold)
