"""Detection replay: score fold by fold, control false alarms, group into incidents.

The replay never reads ahead. Detectors score one fold at a time from data up to
each tick, and the alarm and grouping logic then walks the fold tick by tick.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import numpy.typing as npt

from revwatch.alarms import bh_reject, persistent
from revwatch.detectors.base import Detector
from revwatch.incidents import Grouper, Reading
from revwatch.models import Alarm, BoolArray, FloatArray, Incident, Slice
from revwatch.policy import DetectionPolicy
from revwatch.warehouse import METRICS, MetricDef, Panel


class World(Protocol):
    """What the replay needs from a world. `Warehouse` satisfies it."""

    n_hours: int
    world_hash: str

    def panel(self, metric: MetricDef) -> Panel: ...

    def calendar_flags(self, slices: Sequence[Slice]) -> dict[str, BoolArray]: ...


@dataclass(frozen=True)
class DetectionRun:
    detector: str
    incidents: list[Incident]  # every alert raised, including ones later merged into another
    n_hours: int
    scored_from: int
    # Per metric: scored-period ticks where a source table was missing rows, for some or
    # all slices. Quiet there means blind.
    gaps: Mapping[str, tuple[int, ...]] = field(default_factory=dict)


def _predicted(panel: Panel, expected: FloatArray, lo: int, hi: int) -> FloatArray:
    """Expected numerator per slice and tick: for a rate, the expected rate x the denominator."""
    predicted = expected[:, lo:hi]
    return predicted * panel.den[:, lo:hi] if panel.metric.kind == "rate" else predicted


def numerator_deviation(panel: Panel, expected: FloatArray, tick: int, window: int) -> FloatArray:
    """Observed minus expected numerator per slice, summed over the last `window` ticks.

    For a rate the expected numerator is the expected rate times the observed
    denominator, so the result is in orders, payments or euros and can be
    compared between a slice and the slices inside it.
    """
    lo = max(0, tick - window + 1)
    difference = panel.num[:, lo : tick + 1] - _predicted(panel, expected, lo, tick + 1)
    deviation: FloatArray = np.nansum(difference, axis=1)
    return deviation


def read(
    panel: Panel,
    expected: FloatArray,
    strength: FloatArray,
    pvalue: FloatArray,
    tick: int,
    window: int,
) -> Reading:
    """What the grouper needs to know about one metric at `tick`, over the last `window` ticks.

    `expected`, `strength` and `pvalue` are (slices, ticks) arrays filled up to `tick`.
    """
    lo = max(0, tick - window + 1)
    predicted = _predicted(panel, expected, lo, tick + 1)
    seen = np.isfinite(panel.num[:, lo : tick + 1] - predicted)  # the ticks the deviation counts
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # slices with no strength in the window
        mean_strength = np.nan_to_num(np.nanmean(strength[:, lo : tick + 1], axis=1))
    return Reading(
        deviation=numerator_deviation(panel, expected, tick, window),
        expected=np.where(seen, predicted, 0.0).sum(axis=1),
        volume=(
            np.where(seen, panel.den[:, lo : tick + 1], 0.0).sum(axis=1)
            if panel.metric.kind == "rate"
            else None
        ),
        strength=mean_strength,
        pvalue=pvalue[:, tick],
    )


def run_detection(
    world: World,
    detector: Detector,
    policy: DetectionPolicy | None = None,
    confirm: Callable[[Incident], bool] | None = None,
    progress: Callable[[str], None] | None = None,
) -> DetectionRun:
    """Replay detection over a world.

    `confirm` stands in for a reviewer. After each fold, incidents it accepts are
    marked so that later folds do not use those slice-hours as reference or
    training data. Incidents it rejects, or never sees, stay in the history.
    """
    policy = policy or DetectionPolicy()
    longest = max(detector.windows)
    if longest > policy.evidence_hours:
        raise ValueError(
            f"{detector.name} tests windows of up to {longest} hours, but evidence_hours is "
            f"{policy.evidence_hours}: an alarm could not be explained from the hours behind it"
        )
    panels = {m.name: world.panel(m) for m in METRICS.values() if m.detected}
    names = list(panels)
    flags = {name: world.calendar_flags(panels[name].slices) for name in names}
    index = {name: {s: i for i, s in enumerate(panels[name].slices)} for name in names}
    expected = {name: np.full(panels[name].num.shape, np.nan) for name in names}
    strength = {name: np.full(panels[name].num.shape, np.nan) for name in names}
    pvalue = {name: np.full(panels[name].num.shape, np.nan) for name in names}
    passed: dict[str, npt.NDArray[np.int8]] = {
        name: np.zeros(panels[name].num.shape, dtype=np.int8) for name in names
    }
    exclude = {name: np.zeros(panels[name].num.shape, dtype=bool) for name in names}

    grouper = Grouper(
        policy,
        world.world_hash,
        {name: panels[name].slices for name in names},
        read=lambda metric, tick: read(
            panels[metric],
            expected[metric],
            strength[metric],
            pvalue[metric],
            tick,
            policy.evidence_hours,
        ),
    )

    for start in range(policy.warmup_hours, world.n_hours, policy.fold_hours):
        stop = min(start + policy.fold_hours, world.n_hours)
        for name in names:
            panel = panels[name]
            scores = detector.score(panel, flags[name], start, stop, exclude[name])
            expected[name][:, start:stop] = scores.expected
            strength[name][:, start:stop] = scores.strength
            volume = scores.expected if panel.metric.kind == "count" else panel.den[:, start:stop]
            eligible = (
                np.isfinite(scores.pvalue)
                & np.isfinite(scores.strength)
                & (volume >= panel.metric.min_volume)
            )
            pvalue[name][:, start:stop] = np.where(eligible, scores.pvalue, np.nan)

        for tick in range(start, stop):
            alarms: list[Alarm] = []
            for name in names:
                panel = panels[name]
                # One family of tests per metric, the level shared out between the metrics.
                # Tested together, a promotion that floods one metric with real
                # discoveries loosens the bar for noise on all the others.
                hit = bh_reject(pvalue[name][:, tick], policy.fdr_q / len(names))
                passed[name][:, tick] = np.where(hit, np.sign(strength[name][:, tick]), 0).astype(
                    np.int8
                )
                direction = persistent(
                    passed[name], tick, policy.persistence_need, policy.persistence_window
                )
                rows = np.nonzero(direction)[0]
                if rows.size == 0:
                    continue
                deviation = numerator_deviation(panel, expected[name], tick, policy.evidence_hours)
                for row in rows:
                    alarms.append(
                        Alarm(
                            metric=name,
                            slice=panel.slices[row],
                            tick=tick,
                            direction=int(direction[row]),
                            strength=float(strength[name][row, tick]),
                            deviation=float(deviation[row]),
                        )
                    )
            grouper.step(tick, alarms)

        if confirm is not None:
            for incident in grouper.incidents:
                if confirm(incident):
                    lo = max(0, incident.opened_tick - policy.confirmed_lead_hours)
                    for metric, where, _ in incident.members:
                        exclude[metric][index[metric][where], lo : incident.last_alarm_tick + 1] = (
                            True
                        )
        if progress is not None:
            progress(
                f"{detector.name}: scored hours {start}-{stop} of {world.n_hours}, "
                f"{len(grouper.incidents)} incidents so far"
            )

    gaps: dict[str, tuple[int, ...]] = {}
    for name in names:
        # Some or all slices: a table can be late for one country only.
        missing = np.nonzero(np.isnan(panels[name].num[:, policy.warmup_hours :]).any(axis=0))[0]
        if missing.size:
            gaps[name] = tuple(int(t) + policy.warmup_hours for t in missing)
            if progress is not None:
                progress(
                    f"{detector.name}: {name} not scored for {missing.size} hours without data"
                )
    return DetectionRun(
        detector.name, list(grouper.incidents), world.n_hours, policy.warmup_hours, gaps
    )
