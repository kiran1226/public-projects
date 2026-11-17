"""Repeat runs: the agent recorded again on the same worlds, replayed through today's checks.

One recorded run cannot show how far a model's answers move from run to run.
`data/repeat-runs/` holds further live runs, one folder per run named
`<model>-run<n>`, each with one recording per world exactly as `data/cassettes/`
has for the published run. Replaying them costs nothing and applies the current
checks, so a check added after a run was recorded is tested against what the
model answered then.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from revwatch.investigate.base import Case, Investigator, StopRun
from revwatch.investigation_evals import score_investigation, wrong_proposal
from revwatch.llm import DEFAULT_MODEL
from revwatch.replay import Handled, handle
from revwatch.scenarios import Label

PUBLISHED = "published"
SUPPORT = "CAUSE_NOT_SUPPORTED"  # the check added after the repeat runs were recorded
_NAME = re.compile(r"(?P<model>.+)-run(?P<n>\d+)")


@dataclass(frozen=True)
class Recording:
    """One recorded run: a folder with one file of model responses per world."""

    label: str
    model: str
    directory: Path

    def path(self, world: str) -> Path:
        return self.directory / f"{world}.json"


@dataclass(frozen=True)
class Replayed:
    """One world in one view after replay: the alerts, and what each is an alert on."""

    handled: Sequence[Handled]
    truth: Mapping[str, Label | None]
    ongoing: Mapping[str, Sequence[Label]]  # per alert: the incidents going on when it was raised
    unreplayable: tuple[str, ...] = ()  # alerts whose recording no longer answers; not in `handled`


@dataclass(frozen=True)
class RepeatRun:
    """One run as counts, both worlds together."""

    label: str
    model: str
    known: int  # in isolation: incidents that have a known cause
    right_cause: int
    with_mitigation: int  # in isolation: incidents whose label has a mitigation
    approvable: int  # of those, the proposal passed every check with the right action and target
    alerts: int  # end to end: every alert the detector raised
    unsafe: int  # end to end: wrong proposals that passed every check
    stopped: int  # end to end: wrong proposals a check withheld
    stopped_by_support: tuple[str, ...]  # of those, the alerts that only SUPPORT withheld
    right_held_by_support: tuple[str, ...]  # either view: right proposals SUPPORT withheld
    evidence_ok: int  # end to end: cited values that recompute
    evidence_total: int
    unreplayable: tuple[str, ...] = ()  # either view: investigations left out of every count


def recordings(published: Path, repeats: Path, model: str = DEFAULT_MODEL) -> list[Recording]:
    """The published recording, then every repeat run by model and number."""
    found: list[tuple[str, int, Recording]] = []
    for directory in repeats.iterdir() if repeats.is_dir() else ():
        if not directory.is_dir():
            continue
        name = _NAME.fullmatch(directory.name)
        if name is None:
            raise ValueError(f"{directory.name}: expected <model>-run<n>")
        run = Recording(directory.name, name["model"], directory)
        found.append((name["model"], int(name["n"]), run))
    ordered = sorted(found, key=lambda item: item[:2])
    return [Recording(PUBLISHED, model, published), *(run for _, _, run in ordered)]


def replay_each(
    world: Path, cases: Sequence[Case], investigator: Investigator
) -> tuple[list[Handled], tuple[str, ...]]:
    """Replay case by case: (the alerts that replayed, the ids of those that did not).

    A recording answers one exact request, tool results included. When one
    investigation's recording no longer matches what the tools return, that
    investigation is named and left out, and the rest of the run still replays.
    """
    handled: list[Handled] = []
    lost: list[str] = []
    for case in cases:
        try:
            handled.extend(handle(world, [case], investigator))
        except StopRun:
            lost.append(case.incident_id)
    return handled, tuple(lost)


def _withheld(view: Replayed) -> list[tuple[Handled, bool]]:
    """Every proposal a check withheld, and whether it was the wrong action."""
    found = []
    for item in view.handled:
        proposed = item.investigation.finding.mitigation
        if proposed is None or item.assessment.mitigation is not None:
            continue
        alert_id = item.case.incident_id
        wrong = wrong_proposal(proposed, view.truth[alert_id], view.ongoing.get(alert_id, ()))
        found.append((item, wrong))
    return found


def summarise(
    recording: Recording, end_to_end: Sequence[Replayed], isolated: Sequence[Replayed]
) -> RepeatRun:
    """Count one run. Each sequence has one entry per world."""
    raised = [
        score_investigation("agent", "end to end", view.handled, view.truth, 1.0, view.ongoing)
        for view in end_to_end
    ]
    alone = [
        score_investigation("agent", "in isolation", view.handled, view.truth, 1.0, view.ongoing)
        for view in isolated
    ]
    withheld = [found for view in end_to_end for found in _withheld(view)]
    either = withheld + [found for view in isolated for found in _withheld(view)]
    return RepeatRun(
        label=recording.label,
        model=recording.model,
        known=sum(report.known for report in alone),
        right_cause=sum(report.known_ok for report in alone),
        with_mitigation=sum(report.with_mitigation for report in alone),
        approvable=sum(report.proposals_ok for report in alone),
        alerts=sum(report.alerts for report in raised),
        unsafe=sum(report.unsafe for report in raised),
        stopped=sum(wrong for _, wrong in withheld),
        stopped_by_support=tuple(
            item.case.incident_id
            for item, wrong in withheld
            if wrong and item.assessment.codes == (SUPPORT,)
        ),
        right_held_by_support=tuple(
            item.case.incident_id
            for item, wrong in either
            if not wrong and SUPPORT in item.assessment.codes
        ),
        evidence_ok=sum(report.evidence_ok for report in raised),
        evidence_total=sum(report.evidence_total for report in raised),
        unreplayable=tuple(
            alert_id for view in (*end_to_end, *isolated) for alert_id in view.unreplayable
        ),
    )


def table(runs: Sequence[RepeatRun]) -> str:
    """The runs as a Markdown table, one row per run."""

    def share(done: int, of: int) -> str:
        return f"{done} of {of}" if of else "n/a"

    rows = [
        "| Run | Right cause | Proposal a reviewer can approve as it stands | "
        "Wrong proposals that passed the checks | Wrong proposals a check stopped | "
        f"of those, stopped only by `{SUPPORT}` | Right proposals `{SUPPORT}` held | "
        "Cited values that recompute | Investigations that no longer replay |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run in runs:
        rows.append(
            f"| {run.label} | {share(run.right_cause, run.known)} | "
            f"{share(run.approvable, run.with_mitigation)} | {run.unsafe} | {run.stopped} | "
            f"{len(run.stopped_by_support)} | {len(run.right_held_by_support)} | "
            f"{share(run.evidence_ok, run.evidence_total)} | {len(run.unreplayable)} |"
        )
    return "\n".join(rows)
