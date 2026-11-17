"""Checks on a finding, in code, before anything reaches a reviewer.

A failed check withholds the proposed mitigation and shows the reason. The
incident still reaches the queue with its evidence: no check drops an incident.
All checks always run, so a held finding lists every reason.

What these checks cannot catch: a finding whose evidence is real but whose
reasoning is wrong passes all of them. That is measured as root-cause accuracy,
and it is why a person approves every mitigation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal

import numpy as np

from revwatch.findings import ALLOWED_MITIGATION, ENTITY_KEY, Finding
from revwatch.investigate.base import Case, Investigation
from revwatch.models import DIMENSIONS
from revwatch.policy import ReviewPolicy
from revwatch.scenarios import TARGET_KEYS, Mitigation
from revwatch.tools import SEASONAL_WEEKS, WEEK, Toolbox, overlaps
from revwatch.verify import Check, verify
from revwatch.warehouse import METRICS, TABLE_DIMS

Severity = Literal["page", "ticket", "digest", "none"]

# Reason code -> why the check exists. The README and the review page print these.
REASONS: dict[str, str] = {
    "EVIDENCE_NOT_VERIFIED": "A cited value does not recompute, or its tool call is not in "
    "this investigation. The brief must not contain invented numbers",
    "CAUSE_NOT_IN_CHANGE_LOG": "The blamed change does not exist. The model must not invent one",
    "CAUSE_AFTER_ONSET": "The blamed change is later than the alert's first hour. A cause "
    "precedes its effect",
    "MITIGATION_NOT_ALLOWED": "The action is not the one the catalogue allows for this cause",
    "TARGET_NOT_IN_EVIDENCE": "The target is not what the blamed change is about, or was never "
    "looked up in this investigation. Stops an action against something named only in "
    "untrusted text",
    "CAUSE_NOT_SUPPORTED": "No change is blamed and no verified number shows the alert's metric "
    "moving the alert's way where the action would be taken. A cause inferred with no change "
    "behind it must rest on evidence about this alert",
    "EXPECTED_EVENT": "A planned event that moves this metric this way covers the alert. "
    "Planned events are not incidents",
    "LOW_CONFIDENCE": "Only a well-supported cause may carry a proposal",
    "BUDGET_EXHAUSTED": "The investigation ran out of tool calls",
    "INVESTIGATION_ERROR": "The model call failed, was refused, or returned no valid finding",
}

# What each kind of planned event moves, and which way (0: either way).
EVENT_MOVES: dict[str, dict[str, int]] = {
    "planned_promotion": {
        "sessions": 1,
        "checkout_conversion": 1,
        "discount_rate": 1,
        "gross_margin_rate": -1,
    },
    "planned_campaign": {"sessions": 1, "checkout_conversion": 0},
    "public_holiday": {"sessions": 0, "checkout_conversion": 0},
    "maintenance": dict.fromkeys(METRICS, 0),
}

OWNER: dict[str, str] = {
    "payment_provider_failure": "payments",
    "coupon_misconfiguration": "marketing",
    "price_error": "pricing",
    "bot_traffic": "platform",
    "tracking_outage": "analytics",
    "campaign_change": "marketing",
    "expected_event": "none",
    "unknown": "on-call",
}
# Causes that lose no revenue: what is counted changes, not what customers do.
TICKET_BY_CATEGORY = frozenset({"bot_traffic", "tracking_outage"})


@dataclass(frozen=True)
class Held:
    code: str
    detail: str


@dataclass(frozen=True)
class Assessment:
    """A finding after the checks: what may be proposed, how urgent it is, who owns it."""

    reasons: tuple[Held, ...]
    mitigation: Mitigation | None  # the proposal that passed, or None when held or absent
    severity: Severity
    impact_eur_per_hour: float
    owner: str
    checks: tuple[Check, ...] = field(default_factory=tuple)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(held.code for held in self.reasons)


def assess(
    case: Case, investigation: Investigation, toolbox: Toolbox, policy: ReviewPolicy | None = None
) -> Assessment:
    policy = policy or ReviewPolicy()
    finding = investigation.finding
    checks = verify(finding, investigation.trace, toolbox, policy.evidence_tolerance)
    reasons = [Held(flag, investigation.detail) for flag in investigation.flags]
    reasons += _evidence(checks) + _cause(case, finding, toolbox)
    reasons += _event(case, toolbox)
    if finding.mitigation is not None:
        reasons += _mitigation(case, finding, investigation, checks, toolbox)
        if finding.confidence != "high":
            reasons.append(Held("LOW_CONFIDENCE", f"confidence is {finding.confidence}"))
    expected = any(held.code == "EXPECTED_EVENT" for held in reasons)
    impact = impact_per_hour(case, toolbox, policy)
    return Assessment(
        reasons=tuple(reasons),
        mitigation=None if reasons else finding.mitigation,
        severity=_severity(finding, impact, expected, policy),
        impact_eur_per_hour=impact,
        owner="none" if expected else owner(finding, case),
        checks=tuple(checks),
    )


def _evidence(checks: list[Check]) -> list[Held]:
    failed = [check for check in checks if not check.ok]
    if not failed:
        return []
    return [Held("EVIDENCE_NOT_VERIFIED", "; ".join(check.why for check in failed))]


def _cause(case: Case, finding: Finding, toolbox: Toolbox) -> list[Held]:
    if finding.change_id is None:
        return []
    blamed = toolbox.warehouse.change(finding.change_id)
    if blamed is None:
        return [Held("CAUSE_NOT_IN_CHANGE_LOG", f"{finding.change_id} is not in the change log")]
    # The alert is raised on the second hour out of band at the earliest, so the
    # incident began before the alert's own hour did.
    if blamed["ts"] >= toolbox.warehouse.hour(case.alert_tick):
        return [
            Held(
                "CAUSE_AFTER_ONSET",
                f"{finding.change_id} was made at {blamed['ts']:%Y-%m-%d %H:%M}, after the "
                "incident began",
            )
        ]
    return []


def _event(case: Case, toolbox: Toolbox) -> list[Held]:
    hour = toolbox.warehouse.hour(case.alert_tick)
    for event in toolbox.warehouse.events(hour, hour + timedelta(hours=1)):
        moved = EVENT_MOVES.get(str(event["kind"]), {}).get(case.metric)
        if moved in (0, case.direction) and overlaps(event, case.slice.as_dict()):
            return [
                Held("EXPECTED_EVENT", f"{event['event_id']} ({event['kind']}): {event['note']}")
            ]
    return []


def _mitigation(
    case: Case,
    finding: Finding,
    investigation: Investigation,
    checks: list[Check],
    toolbox: Toolbox,
) -> list[Held]:
    mitigation = finding.mitigation
    assert mitigation is not None
    reasons: list[Held] = []
    allowed = ALLOWED_MITIGATION[finding.cause]
    if mitigation.action != allowed:
        reasons.append(
            Held(
                "MITIGATION_NOT_ALLOWED",
                f"{finding.cause} allows {allowed or 'no action'}, not {mitigation.action}",
            )
        )
    problem = _target(case, finding, investigation, checks, toolbox)
    if problem:
        reasons.append(Held("TARGET_NOT_IN_EVIDENCE", problem))
    if finding.change_id is None and not _supported(
        case, mitigation, investigation, checks, toolbox
    ):
        side = "above" if case.direction > 0 else "below"
        place = ", ".join(f"{key}={value}" for key, value in sorted(mitigation.target.items()))
        reasons.append(
            Held(
                "CAUSE_NOT_SUPPORTED",
                f"no change is blamed, and no verified citation shows {case.metric} {side} "
                f"expected for {place}",
            )
        )
    return reasons


def _target(
    case: Case,
    finding: Finding,
    investigation: Investigation,
    checks: list[Check],
    toolbox: Toolbox,
) -> str:
    """Why the target cannot be trusted, or '' if it can.

    A value in the target is trusted when it comes from one of three places:
    the entity of the blamed change, if that change was retrieved in this
    investigation; the alert's own slice, which code produced; or a row that a
    verified citation points at, such as a provider in a breakdown.
    """
    mitigation = finding.mitigation
    assert mitigation is not None
    wanted = TARGET_KEYS[mitigation.action]
    if sorted(mitigation.target) != sorted(wanted):
        return f"{mitigation.action} needs {', '.join(wanted)}"
    retrieved = {
        row.get("key"): row
        for call in investigation.trace
        if call.name == "change_log"
        for row in call.rows
    }
    cited = {check.citation.row for check in checks if check.ok}
    alert = case.slice.as_dict()
    entity_key = ENTITY_KEY.get(mitigation.action)
    for key, value in mitigation.target.items():
        if key == entity_key and finding.change_id is not None:
            blamed = retrieved.get(finding.change_id)
            if blamed is None:
                return f"{finding.change_id} was never retrieved in this investigation"
            if blamed["entity"] != value:
                return (
                    f"{key} {value!r} is not the entity of {finding.change_id} ({blamed['entity']})"
                )
            continue
        if key in DIMENSIONS and alert.get(key) == value:
            continue
        if value in cited:
            continue
        return f"{key} {value!r} is in no verified evidence"
    return ""


def _supported(
    case: Case,
    mitigation: Mitigation,
    investigation: Investigation,
    checks: list[Check],
    toolbox: Toolbox,
) -> bool:
    """Whether cited evidence ties a cause that has no change behind it to this alert.

    A blamed change is what stands behind a cause, and the checks on it are above.
    With none, the cause is inferred from the cited numbers alone, so one of them
    has to be about this alert and about the place the action would be taken: a
    verified citation of the alert's metric, in a row that names a value of the
    target and none that differs from it, that moved the way the alert did. A row
    of a breakdown is the call's slice plus its own value; an hour of a series is
    the call's slice. Each call is run again, as the verifier does, and the move is
    read from the row and not from the cited column: a brief may cite `window`.
    """
    calls = {call.call_id: call for call in investigation.trace}
    for check in checks:
        call = calls.get(check.citation.call_id)
        if not check.ok or call is None or call.args.get("metric") != case.metric:
            continue
        place = dict(call.args.get("slice") or {})
        again, row = toolbox.rerun(call), check.citation.row
        if call.name == "breakdown":
            place[str(call.args.get("dimension"))] = row
            moved = again.value(row, "change")
        elif call.name == "metric_series":
            observed, expected = again.value(row, "observed"), again.value(row, "expected")
            moved = None if expected is None else observed - expected
        else:
            continue
        on_target = overlaps(place, mitigation.target) and any(
            place.get(key) == value for key, value in mitigation.target.items()
        )
        if on_target and moved is not None and moved * case.direction > 0:
            return True
    return False


def owner(finding: Finding, case: Case) -> str:
    if finding.cause == "release_regression":
        platform = finding.affected_slice.get("platform") or case.slice.as_dict().get(
            "platform", ""
        )
        return "mobile" if platform.startswith("app_") else "web"
    return OWNER[finding.cause]


def impact_per_hour(case: Case, toolbox: Toolbox, policy: ReviewPolicy) -> float:
    """The larger of the revenue and the margin shortfall per hour, in euros.

    Measured for the alert's slice over the hours behind the alert, against the
    usual value of each hour: the median of the same hour of week over the six
    weeks before. A dimension the orders do not have (the payment method) is
    dropped, so a card outage is sized on all orders of its country.
    """
    hi = case.now
    lo = max(0, hi - policy.impact_hours)
    weeks = min(SEASONAL_WEEKS, lo // WEEK)
    if weeks == 0:
        return 0.0
    where = {d: v for d, v in case.slice.as_dict().items() if d in TABLE_DIMS["orders"]}
    margin = METRICS["gross_margin_rate"]  # numerator: margin in euros; denominator: net revenue
    now_margin, now_revenue = toolbox.warehouse.series(margin, where, lo, hi)
    earlier = [
        toolbox.warehouse.series(margin, where, lo - WEEK * k, hi - WEEK * k)
        for k in range(1, weeks + 1)
    ]
    usual_margin = np.median([m for m, _ in earlier], axis=0)
    usual_revenue = np.median([r for _, r in earlier], axis=0)
    shortfall = max(
        float((usual_revenue - now_revenue).sum()), float((usual_margin - now_margin).sum())
    )
    return max(0.0, shortfall) / (hi - lo)


def _severity(finding: Finding, impact: float, expected: bool, policy: ReviewPolicy) -> Severity:
    if expected or finding.cause == "expected_event":
        return "none"
    if finding.cause in TICKET_BY_CATEGORY:
        return "ticket"
    if impact >= policy.page_eur_per_hour:
        return "page"
    return "ticket" if impact >= policy.ticket_eur_per_hour else "digest"
