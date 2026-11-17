"""Alarm and grouping thresholds. Tune on the dev world only and log every change.

What is not here, and why: each detector keeps its own settings (history
lengths, test windows) next to the code they belong to, and the volume floor of
a metric is part of its definition in `warehouse.METRICS`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DetectionPolicy:
    fdr_q: float = 0.05  # Benjamini-Hochberg level per tick, shared out between the metrics
    persistence_need: int = 2  # a slice alarms when it passes this many times...
    persistence_window: int = 3  # ...within this many consecutive ticks
    # Deviations and strengths are read over this many ticks. Not fewer than the longest
    # window a detector tests: an alarm must be explained from the hours that raised it.
    evidence_hours: int = 4
    # Grouping. Strengths are in units of the detector's own band: 1 is its edge.
    # A slice is a root, not part of a broader one, when its broader slices explain less
    # than `ripple_share` of its move (it moved over three times as much as they did)
    # and the unexplained part alone is at least `separate_strength` bands out.
    ripple_share: float = 0.33
    separate_strength: float = 2.0
    explain_share: float = 0.8  # a narrower slice holding this share of a deviation explains it
    link_gap_hours: int = 3  # how long after its last alarm a slice still speaks for its incident
    close_after_quiet_hours: int = 6  # an incident closes after this many quiet ticks
    # Replay.
    confirmed_lead_hours: int = 3  # a confirmed incident began a little before its first alarm
    warmup_hours: int = 56 * 24  # weeks 1 to 8 are never scored
    fold_hours: int = 7 * 24  # detectors are refitted once per fold


@dataclass(frozen=True)
class ReviewPolicy:
    """Investigation, guardrail and severity settings. The amounts are assumptions."""

    tool_budget: int = 12  # tool calls an investigation may make
    evidence_tolerance: float = 0.01  # a cited value must recompute within this share
    # Hours before the alert that an investigation looks at. Slow incidents are found
    # five to seven hours after they start.
    lookback_hours: int = 12
    # Severity, from the larger of the revenue and the margin shortfall per hour.
    page_eur_per_hour: float = 2000.0
    ticket_eur_per_hour: float = 200.0
    impact_hours: int = 2  # the shortfall is measured over the two hours that raised the alert
