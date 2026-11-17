"""Score detection against the labelled scenarios and write the report."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from revwatch.models import Incident
from revwatch.pipeline import DetectionRun
from revwatch.scenarios import Label
from revwatch.warehouse import METRICS

GRACE_HOURS = 3  # an alert this soon after an incident ends still counts as detecting it

# Evaluation metric -> (definition, why this metric). The report prints both.
WHY: dict[str, tuple[str, str]] = {
    "Incident recall": (
        "Injected incidents with an alert that opens in their window, names a related slice, "
        "and is about a metric the incident moves, in that direction",
        "A missed incident is the expensive error; counting per incident stops one long "
        "outage dominating",
    ),
    "False incidents per week": (
        "Alerts that match no injected incident",
        "On-call cost is felt per page, and a precision figure hides the volume",
    ),
    "False incidents on a benign look-alike": (
        "False incidents that opened during a labelled benign event, on a related slice: a "
        "planned promotion or campaign, a public holiday, late data, an organic level shift",
        "Separates alerts with a reason someone can look up, in the calendar or the load "
        "log, from noise that has none and wears an on-call team down",
    ),
    "Duplicate alerts": (
        "Further alerts on an incident that was already detected",
        "Shows whether grouping holds one incident together or pages for it repeatedly",
    ),
    "Missed while an older alert was open": (
        "Missed incidents for which an alert on a related slice had opened earlier and was "
        "still alarming",
        "Separates a detector that saw nothing from one whose earlier alert already covered "
        "that part of the business, so nobody is paged again",
    ),
    "Time to detect": (
        "Median hours from onset to the alert, over detected incidents. An alert on an hour "
        "can only be raised once that hour is over",
        "Loss grows every hour, so a late detection is close to a miss",
    ),
    "Localisation accuracy": (
        "Detected incidents whose named slice equals the label",
        "A correct alarm on the wrong slice goes to the wrong team",
    ),
}

REJECTED: dict[str, str] = {
    "Accuracy": "Over 99% of slice-hours are normal, so a detector that never alarms "
    "scores above 99%.",
    "Point-wise precision, recall and F1": "They score every hour separately, so a long "
    "outage outweighs many short ones and flagging hour 30 earns as much as flagging hour 2.",
    "AUROC": "It has no operating point, and with this imbalance it stays high while the "
    "pager is unusable.",
}


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    kind: str
    alerts: int
    hours_to_detect: float | None
    localised: bool | None
    buried: bool = False  # missed, while an older alert on a related slice was still open


@dataclass(frozen=True)
class DetectionReport:
    detector: str
    weeks: float
    n_incidents: int
    detected: int
    false_incidents: int
    false_on_benign: int  # of the false incidents, those on a labelled benign event
    duplicates: int
    buried: int
    median_hours_to_detect: float | None
    localised: int
    results: tuple[ScenarioResult, ...]
    gaps: Mapping[str, tuple[datetime, ...]] = field(default_factory=dict)


def matches(incident: Incident, label: Label, start: datetime) -> bool:
    """Whether the scenario behind `label` can have drawn this alert.

    The alert opens in the label's window, names a related slice, and is about
    a metric the scenario moves, in the direction it moves it. Window and slice
    alone are not enough: the overall series is related to every slice, so any
    alert named after it would count for whatever was going on at the time.

    Late data has no slice: it is a table that arrived late. Only an alert on a
    metric built on that table can have been drawn by it.
    """
    opened = start + timedelta(hours=incident.opened_tick)
    in_window = label.start <= opened < label.end + timedelta(hours=GRACE_HOURS)
    if label.moves:
        moved = label.moves.get(incident.metric)
        if moved is None or moved not in (incident.direction, 0):
            return False
    if label.drop_table is not None:
        metric = METRICS[incident.metric]
        sources = {metric.num[0]} | ({metric.den[0]} if metric.den else set())
        if label.drop_table not in sources:
            return False
    return in_window and incident.slice.related(label.label_slice)


def _buried(label: Label, incidents: Sequence[Incident], start: datetime) -> bool:
    """Whether an older alert on a related slice was still alarming when `label` began.

    The overall series is related to every slice, so it does not count.
    """
    onset = (label.start - start).total_seconds() / 3600
    where = label.label_slice
    return any(
        incident.opened_tick < onset <= incident.last_alarm_tick
        and any(member.items and member.related(where) for _, member, _ in incident.members)
        for incident in incidents
    )


def confirm_from_labels(labels: Sequence[Label], start: datetime) -> Callable[[Incident], bool]:
    """A stand-in reviewer for the replay: confirms alerts that match a real incident."""
    real = [label for label in labels if label.kind == "incident"]
    return lambda incident: any(matches(incident, label, start) for label in real)


def score_detection(run: DetectionRun, labels: Sequence[Label], start: datetime) -> DetectionReport:
    scored = [label for label in labels if label.labelled]
    incidents = sorted(
        (i for i in run.incidents if i.opened_tick >= run.scored_from),
        key=lambda i: (i.opened_tick, i.incident_id),
    )
    first: dict[str, Incident] = {}
    alerts: Counter[str] = Counter()
    false_incidents = false_on_benign = duplicates = 0
    for incident in incidents:
        hits = [label for label in scored if matches(incident, label, start)]
        for label in hits:
            alerts[label.scenario_id] += 1
        real = [label for label in hits if label.kind == "incident"]
        if not real:
            false_incidents += 1
            false_on_benign += bool(hits)
            continue
        new = [label for label in real if label.scenario_id not in first]
        if not new:
            duplicates += 1
        for label in new:
            first[label.scenario_id] = incident

    results: list[ScenarioResult] = []
    for label in scored:
        found = first.get(label.scenario_id)
        if found is None:
            buried = label.kind == "incident" and _buried(label, run.incidents, start)
            results.append(
                ScenarioResult(
                    label.scenario_id, label.kind, alerts[label.scenario_id], None, None, buried
                )
            )
            continue
        onset = (label.start - start).total_seconds() / 3600
        results.append(
            ScenarioResult(
                label.scenario_id,
                label.kind,
                alerts[label.scenario_id],
                # The alert is on tick `opened_tick`, whose data is complete an hour later.
                float(found.opened_tick + 1 - onset),
                found.slice == label.label_slice,
            )
        )
    hours = [r.hours_to_detect for r in results if r.hours_to_detect is not None]
    return DetectionReport(
        detector=run.detector,
        weeks=(run.n_hours - run.scored_from) / 168,
        n_incidents=sum(label.kind == "incident" for label in scored),
        detected=len(first),
        false_incidents=false_incidents,
        false_on_benign=false_on_benign,
        duplicates=duplicates,
        buried=sum(r.buried for r in results),
        median_hours_to_detect=float(np.median(hours)) if hours else None,
        localised=sum(1 for r in results if r.localised),
        results=tuple(results),
        gaps={
            metric: tuple(start + timedelta(hours=tick) for tick in ticks)
            for metric, ticks in run.gaps.items()
        },
    )


def _cell(result: ScenarioResult) -> str:
    if result.kind == "benign":
        if result.alerts == 0:
            return "quiet"
        return f"{result.alerts} alert" + ("s" if result.alerts != 1 else "")
    if result.hours_to_detect is None:
        return "missed, older alert open" if result.buried else "missed"
    return f"{result.hours_to_detect:.0f} h, " + (
        "exact slice" if result.localised else "wrong slice"
    )


def _gap_lines(report: DetectionReport) -> list[str]:
    if not report.gaps:
        return ["None."]
    lines = [
        "A source table was missing rows for these hours, so the slices that need those rows "
        "were not scored. Quiet there means blind, not healthy.",
        "",
    ]
    for metric, hours in report.gaps.items():
        lines.append(
            f"- {metric}: {len(hours)} hour{'s' if len(hours) != 1 else ''}, "
            f"{hours[0]:%Y-%m-%d %H:%M} to {hours[-1]:%Y-%m-%d %H:%M}"
        )
    return lines


def _seed_lines(
    other_seeds: Mapping[int, Sequence[DetectionReport]], columns: str, n: int
) -> list[str]:
    lines = [
        "## Other seeds",
        "",
        "The detectors and their thresholds were tuned on the world above. These are the same "
        "scenarios on worlds generated from other seeds: recall, and false incidents per week.",
        "",
        f"| Seed | {columns} |",
        "|---|" + "---|" * n,
    ]
    for seed, seed_reports in other_seeds.items():
        cells = " | ".join(
            f"{r.detected} of {r.n_incidents}, {r.false_incidents / r.weeks:.1f} per week"
            for r in seed_reports
        )
        lines.append(f"| {seed} | {cells} |")
    return [*lines, ""]


def render_report(
    reports: Sequence[DetectionReport],
    world: str,
    world_hash: str,
    other_seeds: Mapping[int, Sequence[DetectionReport]] | None = None,
    investigation: Sequence[str] = (),
    held_out: bool = False,
) -> str:
    """The report for one world. `investigation` is that section, already rendered.

    The held-out world's report follows the development world's in the same
    file, so it has its own top heading and leaves out what is said once above.
    """
    names = [report.detector for report in reports]
    columns = " | ".join(f"`{name}`" for name in names)

    def row(metric: str, cells: Sequence[str]) -> str:
        return f"| {metric} | " + " | ".join(cells) + f" | {WHY[metric][1]} |"

    size = (
        f"World `{world}` (hash `{world_hash}`), {reports[0].weeks:.0f} scored weeks, "
        f"{reports[0].n_incidents} injected incidents."
    )
    if held_out:
        head = [
            "# Held-out world",
            "",
            f"{size} Its scenarios were written after the detectors, their thresholds, the "
            "grouping rules, the scripted investigator and the agent's prompt were frozen, "
            "and none of those has been changed since. The check `CAUSE_NOT_SUPPORTED` was "
            "added afterwards, after a repeat run on the development world, and was replayed "
            "against recordings from this world before it was kept. So this world vouches "
            "for detection and for the two investigators, not for that check.",
            "",
        ]
    else:
        head = [
            "# Evaluation report",
            "",
            f"Generated by `revwatch eval`. {size}",
            "",
            "The world is synthetic and small. Read counts, not decimals.",
            "",
        ]
    appendix = [
        "## Metrics not used",
        "",
        *[f"- **{name}.** {reason}" for name, reason in REJECTED.items()],
        "",
        "## Watched metrics",
        "",
        "| Metric | Definition | Why this metric |",
        "|---|---|---|",
        *[f"| {m.name} | {m.definition} | {m.why} |" for m in METRICS.values()],
        "",
    ]
    lines = [
        *head,
        "## Detection",
        "",
        f"| Metric | {columns} | Why this metric |",
        "|---|" + "---:|" * len(names) + "---|",
        row("Incident recall", [f"{r.detected} of {r.n_incidents}" for r in reports]),
        row("False incidents per week", [f"{r.false_incidents / r.weeks:.1f}" for r in reports]),
        row(
            "False incidents on a benign look-alike",
            [f"{r.false_on_benign} of {r.false_incidents}" for r in reports],
        ),
        row("Duplicate alerts", [str(r.duplicates) for r in reports]),
        row("Missed while an older alert was open", [str(r.buried) for r in reports]),
        row(
            "Time to detect",
            [
                "n/a" if r.median_hours_to_detect is None else f"{r.median_hours_to_detect:.1f} h"
                for r in reports
            ],
        ),
        row("Localisation accuracy", [f"{r.localised} of {r.detected}" for r in reports]),
        "",
        "Definitions:",
        "",
        *[f"- **{name}**: {definition}." for name, (definition, _) in WHY.items()],
        "",
        *(_seed_lines(other_seeds, columns, len(names)) if other_seeds else []),
        *investigation,
        "## Hours not scored",
        "",
        *_gap_lines(reports[0]),
        "",
        *([] if held_out else appendix),
        "## Scenario by scenario",
        "",
        "For an incident: hours to detect and whether the named slice was exact. "
        "For a benign look-alike: how many alerts it drew.",
        "",
        f"| Scenario | Kind | {columns} |",
        "|---|---|" + "---|" * len(names),
    ]
    for i, result in enumerate(reports[0].results):
        cells = " | ".join(_cell(report.results[i]) for report in reports)
        lines.append(f"| {result.scenario_id} | {result.kind} | {cells} |")
    return "\n".join(lines) + "\n"
