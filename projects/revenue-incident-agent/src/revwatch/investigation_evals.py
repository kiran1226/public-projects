"""Score investigations against the labelled scenarios.

Two views. End to end: the alerts a detector raised, each investigated as it
was raised. In isolation: one case per injected incident, built from its
label, so that what the detector missed or misnamed does not count against the
investigator.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from revwatch.evals import GRACE_HOURS, matches
from revwatch.investigate.base import Case
from revwatch.models import Incident, Slice
from revwatch.replay import Handled
from revwatch.scenarios import Label, Mitigation
from revwatch.warehouse import METRICS

# Evaluation metric -> (definition, why this metric). The report prints both.
WHY: dict[str, tuple[str, str]] = {
    "Root-cause accuracy": (
        "Alerts on an incident with a known cause where the finding names that cause and "
        "the change-log entry behind it",
        "This is the claim people act on",
    ),
    "Correct declines": (
        "Alerts on an incident with no known cause where the finding says `unknown`",
        "A confident wrong cause is worse than none",
    ),
    "Evidence validity": (
        "Cited values that recompute within 1% when the tool call is run again",
        "Measures invented numbers directly",
    ),
    "Correct proposals": (
        "Alerts on an incident that has a mitigation where the proposal passed every check "
        "and names the right action and target",
        "What a reviewer can approve without doing the investigation again",
    ),
    "Unsafe mitigation proposals": (
        "Proposals that passed every check and are not the right action and target for any "
        "incident going on when the alert was raised",
        "The one error with a direct cost. It must be zero",
    ),
    "False pages per week": (
        "Alerts on no incident that would page: severity `page`, and not explained by a "
        "planned event",
        "What the on-call experiences at night",
    ),
    "Tool calls per alert": (
        "Mean number of tool calls an investigation made",
        "The budget is 12; an investigator that needs them all is one change away from running out",
    ),
    "Cost per alert": (
        "Mean model cost of an investigation, from recorded token usage",
        "The agent has to earn its cost against the script, which is free",
    ),
    "Model time per alert": (
        "Mean seconds spent waiting for the model in an investigation",
        "An on-call needs the brief within minutes of the alert",
    ),
}

# The metric an incident's case is built on in the isolated view: the first of these
# that the scenario moves. The most specific signal comes first.
PRIMARY = (
    "payment_success_rate",
    "discount_rate",
    "gross_margin_rate",
    "sessions",
    "checkout_conversion",
)
ALERT_AFTER_HOURS = 2  # an alert needs two hours out of band, so it is about the third hour


@dataclass(frozen=True)
class CaseScore:
    incident_id: str
    scenario_id: str | None  # the incident it is an alert on, if any
    expected: str | None
    found: str
    cause_ok: bool | None  # None when the alert is on no incident
    # correct; unsafe; held; none; redundant (right for an incident this alert is not on)
    proposal: str
    held: tuple[str, ...]
    severity: str


@dataclass(frozen=True)
class InvestigationReport:
    investigator: str
    view: str  # "end to end" or "in isolation"
    model: str
    weeks: float
    alerts: int
    known: int  # alerts on an incident with a known cause
    known_ok: int
    unknown: int  # alerts on an incident with no known cause
    unknown_ok: int
    evidence_ok: int
    evidence_total: int
    with_mitigation: int  # alerts on an incident whose label has a mitigation
    proposals_ok: int
    unsafe: int
    false_pages: int
    tool_calls: float
    cost_usd: float
    model_seconds: float
    errors: int
    held: Mapping[str, int]
    rows: tuple[CaseScore, ...]


def cases_from_labels(labels: Sequence[Label], start: datetime) -> dict[str, Case]:
    """One case per labelled incident, keyed by scenario id: the alert a perfect detector raises."""
    cases: dict[str, Case] = {}
    for label in labels:
        if not (label.labelled and label.kind == "incident" and label.moves):
            continue
        metric = next(name for name in PRIMARY if name in label.moves)
        where = {d: v for d, v in label.slice.items() if d in METRICS[metric].dims}
        onset = int((label.start - start).total_seconds() // 3600)
        cases[label.scenario_id] = Case(
            incident_id=f"GT-{label.scenario_id}",
            metric=metric,
            slice=Slice.from_dict(where),
            direction=label.moves[metric] or -1,
            alert_tick=onset + ALERT_AFTER_HOURS,
        )
    return cases


def labels_for(
    incidents: Sequence[Incident], labels: Sequence[Label], start: datetime
) -> dict[str, Label | None]:
    """Per alert: the injected incident it is an alert on, or None."""
    real = [label for label in labels if label.labelled and label.kind == "incident"]
    return {
        incident.incident_id: next((lb for lb in real if matches(incident, lb, start)), None)
        for incident in incidents
    }


def ongoing_at(
    incidents: Sequence[Incident], labels: Sequence[Label], start: datetime
) -> dict[str, list[Label]]:
    """Per alert: the injected incidents that were going on when it was raised."""
    real = [label for label in labels if label.labelled and label.kind == "incident"]
    found: dict[str, list[Label]] = {}
    for incident in incidents:
        opened = start + timedelta(hours=incident.opened_tick)
        found[incident.incident_id] = [
            label
            for label in real
            if label.start <= opened < label.end + timedelta(hours=GRACE_HOURS)
        ]
    return found


def wrong_proposal(
    proposed: Mitigation, label: Label | None, ongoing: Sequence[Label] = ()
) -> bool:
    """Whether a proposed mitigation is not the right action, whatever became of it.

    The rule `score_investigation` applies to a proposal that passed the checks,
    for one that a check withheld: on an alert on an incident, anything but that
    incident's mitigation is wrong; on an alert on no incident, anything that is
    not the mitigation of an incident going on at the time.
    """
    if label is not None:
        return proposed != label.mitigation
    return all(proposed != other.mitigation for other in ongoing)


def score_investigation(
    investigator: str,
    view: str,
    handled: Sequence[Handled],
    truth: Mapping[str, Label | None],
    weeks: float,
    ongoing: Mapping[str, Sequence[Label]] | None = None,
) -> InvestigationReport:
    """Score `handled` against `truth`: per alert, the incident it is an alert on, or None.

    `ongoing` lists, per alert, the incidents that were going on when it was
    raised. A proposal on an alert that is on no incident is unsafe unless it
    is the right action for one of those: disabling the coupon that is causing
    an incident is not a wrong action because the alert was on a neighbouring
    slice.
    """
    rows: list[CaseScore] = []
    held: Counter[str] = Counter()
    for item in handled:
        label = truth[item.case.incident_id]
        finding, passed = item.investigation.finding, item.assessment.mitigation
        held.update(item.assessment.codes)
        if label is None:
            cause_ok = None
            fits = any(
                passed == other.mitigation
                for other in (ongoing or {}).get(item.case.incident_id, ())
            )
            proposal = "none" if passed is None else "redundant" if fits else "unsafe"
        else:
            cause_ok = finding.cause == label.cause and finding.change_id == label.change_id
            if passed is None:
                proposal = "held" if finding.mitigation is not None else "none"
            else:
                proposal = "correct" if passed == label.mitigation else "unsafe"
        rows.append(
            CaseScore(
                item.case.incident_id,
                label.scenario_id if label else None,
                label.cause if label else None,
                finding.cause,
                cause_ok,
                proposal,
                item.assessment.codes,
                item.assessment.severity,
            )
        )
    n = max(len(handled), 1)
    on = [(row, truth[row.incident_id]) for row in rows if row.scenario_id is not None]
    known = [row for row, label in on if label is not None and label.cause != "unknown"]
    unknown = [row for row, label in on if label is not None and label.cause == "unknown"]
    return InvestigationReport(
        investigator=investigator,
        view=view,
        model=next((h.investigation.model for h in handled if h.investigation.model), ""),
        weeks=weeks,
        alerts=len(handled),
        known=len(known),
        known_ok=sum(bool(row.cause_ok) for row in known),
        unknown=len(unknown),
        unknown_ok=sum(bool(row.cause_ok) for row in unknown),
        evidence_ok=sum(check.ok for h in handled for check in h.assessment.checks),
        evidence_total=sum(len(h.assessment.checks) for h in handled),
        with_mitigation=sum(label is not None and label.mitigation is not None for _, label in on),
        proposals_ok=sum(row.proposal == "correct" for row in rows),
        unsafe=sum(row.proposal == "unsafe" for row in rows),
        false_pages=sum(row.scenario_id is None and row.severity == "page" for row in rows),
        tool_calls=sum(h.investigation.tool_calls for h in handled) / n,
        cost_usd=sum(h.investigation.cost_usd for h in handled) / n,
        model_seconds=sum(h.investigation.model_seconds for h in handled) / n,
        errors=sum("INVESTIGATION_ERROR" in h.investigation.flags for h in handled),
        held=dict(sorted(held.items())),
        rows=tuple(rows),
    )


def _row(metric: str, cells: Sequence[str]) -> str:
    return f"| {metric} | " + " | ".join(cells) + f" | {WHY[metric][1]} |"


def render_investigation(
    reports: Sequence[InvestigationReport], agent_recorded: bool = True
) -> list[str]:
    """The investigation section of the report: one column per investigator and view."""
    if not reports:
        return []
    missing = (
        []
        if agent_recorded
        else [
            "The agent is not in this table: no recorded run exists for this world. "
            "`revwatch eval --live --record` makes one (it needs an API key and spends "
            "credit).",
            "",
        ]
    )
    head = " | ".join(f"`{r.investigator}`, {r.view}" for r in reports)

    def share(done: int, of: int) -> str:
        return f"{done} of {of}" if of else "n/a"

    lines = [
        "## Investigation",
        "",
        "End to end: every alert the `gbm` detector raised, investigated as it was raised. "
        "In isolation: one case per injected incident, built from its label, so that "
        "detection errors do not count.",
        "",
        *missing,
        f"| Metric | {head} | Why this metric |",
        "|---|" + "---:|" * len(reports) + "---|",
        _row("Root-cause accuracy", [share(r.known_ok, r.known) for r in reports]),
        _row("Correct declines", [share(r.unknown_ok, r.unknown) for r in reports]),
        _row("Evidence validity", [share(r.evidence_ok, r.evidence_total) for r in reports]),
        _row("Correct proposals", [share(r.proposals_ok, r.with_mitigation) for r in reports]),
        _row("Unsafe mitigation proposals", [str(r.unsafe) for r in reports]),
        _row(
            "False pages per week",
            [
                f"{r.false_pages / r.weeks:.1f}" if r.view == "end to end" else "n/a"
                for r in reports
            ],
        ),
        _row("Tool calls per alert", [f"{r.tool_calls:.1f}" for r in reports]),
        _row("Cost per alert", [f"${r.cost_usd:.4f}" for r in reports]),
        _row("Model time per alert", [f"{r.model_seconds:.1f} s" for r in reports]),
        "",
        "Definitions:",
        "",
        *[f"- **{name}**: {definition}." for name, (definition, _) in WHY.items()],
        "",
        "Alerts investigated: "
        + "; ".join(f"`{r.investigator}`, {r.view}: {r.alerts}" for r in reports)
        + ". Zero unsafe proposals in n alerts bounds the true rate at roughly 3 / n.",
        "",
        "Checks that held a finding (a held finding still reaches the queue, without its "
        "proposal):",
        "",
    ]
    for report in reports:
        codes = ", ".join(f"{code} {n}" for code, n in report.held.items()) or "none"
        lines.append(f"- `{report.investigator}`, {report.view}: {codes}")
    lines += ["", "### Incident by incident", ""]
    isolated = [r for r in reports if r.view == "in isolation"]
    if isolated:
        names = " | ".join(f"`{r.investigator}`" for r in isolated)
        lines += [
            "In isolation. Expected cause, then what each investigator found and what became "
            "of its proposal.",
            "",
            f"| Incident | Expected | {names} |",
            "|---|---|" + "---|" * len(isolated),
        ]
        for i, first in enumerate(isolated[0].rows):
            cells = " | ".join(_cell(r.rows[i]) for r in isolated)
            lines.append(f"| {first.scenario_id} | {first.expected} | {cells} |")
        lines.append("")
    return lines


def _cell(row: CaseScore) -> str:
    verdict = "right" if row.cause_ok else "wrong"
    proposal = {
        "correct": "proposal correct",
        "unsafe": "PROPOSAL UNSAFE",
        "held": "proposal held (" + ", ".join(row.held) + ")",
        "none": "no proposal",
        "redundant": "proposal correct for another alert's incident",
    }[row.proposal]
    return f"{row.found}: {verdict}; {proposal}"
