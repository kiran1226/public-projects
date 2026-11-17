"""The baseline investigator: a fixed procedure, no model.

It does what a runbook would say. Check the calendar; find when the metric
left its usual value; break it down by every dimension to see where the change
sits; take the latest change-log entry before that hour that can concern the
place and the metric; map the kind of change to a cause.

It is the mode that runs without an API key, and it is what the agent has to
beat. To make that a fair fight it is not naive: it only blames kinds of change
that can move the metric at all, and it says "unknown" when nothing fits.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from revwatch.findings import (
    ALLOWED_MITIGATION,
    CAUSE_OF_CHANGE,
    ENTITY_KEY,
    Citation,
    Confidence,
    Finding,
)
from revwatch.investigate.base import Case, Investigation
from revwatch.policy import ReviewPolicy
from revwatch.scenarios import Cause, Mitigation
from revwatch.tools import Toolbox, ToolResult, overlaps
from revwatch.warehouse import METRICS

# The kinds of change that can move each metric in each direction. A price update does
# not lower payment success, however close in time it is, and no release makes more
# people visit. A move with no entry here has no cause in the change log.
PLAUSIBLE: dict[tuple[str, int], tuple[str, ...]] = {
    ("sessions", -1): ("campaign", "tracking_tag"),
    ("checkout_conversion", -1): ("release", "provider_status"),
    ("checkout_conversion", 1): ("tracking_tag",),  # fewer sessions counted, same orders
    ("payment_success_rate", -1): ("provider_status", "release"),
    ("discount_rate", 1): ("coupon",),
    ("gross_margin_rate", -1): ("price", "coupon"),
    ("gross_margin_rate", 1): ("price",),
}
CHANGE_LEAD_HOURS = 2  # a change is looked for from this long before the metric moved
CONCENTRATED = 0.8  # one value holds this share of the change: the problem is there
BOT_SESSIONS = 1.5  # sessions at this multiple of expected, with conversion down


def _stamp(when: datetime) -> str:
    return f"{when:%Y-%m-%dT%H:%M}"


class ScriptInvestigator:
    name = "script"

    def __init__(self, policy: ReviewPolicy | None = None) -> None:
        self.policy = policy or ReviewPolicy()

    def investigate(self, case: Case, toolbox: Toolbox) -> Investigation:
        return Investigation(self._finding(case, toolbox), toolbox.trace)

    def _finding(self, case: Case, box: Toolbox) -> Finding:
        now = box.warehouse.hour(case.now)
        where = case.slice.as_dict()
        direction = "above" if case.direction > 0 else "below"
        place = case.slice.key

        planned = box.call(
            "calendar", {"start": _stamp(now - timedelta(hours=1)), "end": _stamp(now)}
        )
        events = [row for row in planned.rows if overlaps(row, where)]
        if events:
            return Finding(
                cause="expected_event",
                affected_slice=where,
                confidence="high",
                summary=f"{case.metric} for {place} is {direction} expected during a planned "
                f"event ({events[0]['kind']}: {events[0]['note']}). No action proposed.",
            )

        start = now - timedelta(hours=self.policy.lookback_hours)
        series = box.call(
            "metric_series",
            {"metric": case.metric, "start": _stamp(start), "end": _stamp(now), "slice": where},
        )
        onset = self._onset(series, case.direction) or _stamp(now - timedelta(hours=3))
        evidence = self._cite(series, series.rows[-1]["key"] if series.rows else "", "observed")
        evidence += self._cite(series, series.rows[-1]["key"] if series.rows else "", "expected")

        # Where does the change sit? Narrow the slice while one value carries most of it.
        provider: str | None = None
        for dimension in (*METRICS[case.metric].dims, "provider"):
            if dimension in where:
                continue
            split = box.call(
                "breakdown",
                {
                    "metric": case.metric,
                    "dimension": dimension,
                    "start": onset,
                    "end": _stamp(now),
                    "slice": where,
                },
            )
            top = self._concentrated(split, case.direction)
            if top is None:
                continue
            evidence += self._cite(split, top, "excess")
            if dimension == "provider":
                provider = top
            else:
                where = {**where, dimension: top}

        since = datetime.fromisoformat(onset) - timedelta(hours=CHANGE_LEAD_HOURS)
        until = datetime.fromisoformat(onset) + timedelta(hours=1)
        log = box.call("change_log", {"start": _stamp(since), "end": _stamp(until), "scope": where})
        fitting = [
            row
            for row in log.rows
            if row["kind"] in PLAUSIBLE.get((case.metric, case.direction), ())
            and (provider is None or row["kind"] != "provider_status" or row["entity"] == provider)
        ]
        if fitting:
            blamed = fitting[-1]  # the latest before the metric moved
            cause = CAUSE_OF_CHANGE[blamed["kind"]]
            confidence: Confidence = "high" if len(fitting) == 1 else "medium"
            return Finding(
                cause=cause,
                change_id=blamed["key"],
                affected_slice=where,
                evidence=evidence,
                confidence=confidence,
                mitigation=self._mitigation(cause, blamed, where),
                summary=f"{case.metric} for {place} has been {direction} expected since {onset}. "
                f"The change sits in {_key(where)}. Latest fitting change before that: "
                f"{blamed['key']} ({blamed['kind']} {blamed['entity']}, {blamed['time']}). "
                f"{len(fitting)} fitting change(s) in the window.",
            )

        bots = self._bots(case, box, where, onset, _stamp(now))
        if bots is not None:
            return Finding(
                cause="bot_traffic",
                affected_slice=where,
                evidence=evidence + bots,
                confidence="high",
                mitigation=Mitigation(
                    action="enable_bot_challenge",
                    target={"country": where["country"], "channel": where["channel"]},
                ),
                summary=f"Sessions for {_key(where)} are far above expected since {onset} while "
                "conversion fell: more visits counted, no more orders. No change-log entry "
                "fits. This is the pattern of automated traffic.",
            )
        return Finding(
            cause="unknown",
            affected_slice=where,
            evidence=evidence,
            confidence="low",
            summary=f"{case.metric} for {place} has been {direction} expected since {onset}, "
            f"concentrated in {_key(where)}. No change-log entry of a kind that moves this "
            "metric fits the place and the time. Cause not found.",
        )

    @staticmethod
    def _onset(series: ToolResult, direction: int) -> str | None:
        """The first hour of the unbroken run, up to the alert, on the alert's side of expected.

        "On the alert's side" means at least half as far from expected as the alert
        hour itself, so that ordinary wobble before the incident does not count.
        """
        rows = [
            r for r in series.rows if r["expected"] is not None and r["observed"] == r["observed"]
        ]
        if not rows:
            return None
        gap = [direction * (r["observed"] - r["expected"]) for r in rows]
        if gap[-1] <= 0:
            return None
        first = len(rows) - 1
        while first > 0 and gap[first - 1] >= 0.5 * gap[-1]:
            first -= 1
        return str(rows[first]["key"])

    @staticmethod
    def _concentrated(split: ToolResult, direction: int) -> str | None:
        """The value that carries most of the change in the alert's direction, if one does."""
        moved = {
            r["key"]: direction * r["excess"] for r in split.rows if r["excess"] == r["excess"]
        }
        total = sum(v for v in moved.values() if v > 0)
        if total <= 0:
            return None
        top = max(moved, key=lambda key: moved[key])
        return str(top) if moved[top] >= CONCENTRATED * total else None

    @staticmethod
    def _cite(result: ToolResult, row: str, column: str) -> list[Citation]:
        value = result.value(row, column)
        if not isinstance(value, int | float) or value != value:
            return []
        return [Citation(call_id=result.call_id, row=row, column=column, value=float(value))]

    @staticmethod
    def _mitigation(
        cause: Cause, blamed: Mapping[str, Any], where: Mapping[str, str]
    ) -> Mitigation | None:
        action = ALLOWED_MITIGATION[cause]
        if action is None or action not in ENTITY_KEY:
            return None
        target = {ENTITY_KEY[action]: str(blamed["entity"])}
        if action == "failover_payment_provider":
            country = blamed.get("country") or where.get("country")
            if country is None:
                return None  # a failover is per country; without one there is nothing to propose
            target["country"] = str(country)
        return Mitigation(action=action, target=target)

    def _bots(
        self, case: Case, box: Toolbox, where: Mapping[str, str], onset: str, now: str
    ) -> list[Citation] | None:
        """Evidence for automated traffic, or None: sessions up, conversion down, one place."""
        if case.metric not in ("sessions", "checkout_conversion"):
            return None
        if "country" not in where or "channel" not in where:
            return None
        place = {"country": where["country"], "channel": where["channel"]}
        cited: list[Citation] = []
        ratios: dict[str, float] = {}
        for metric in ("sessions", "checkout_conversion"):
            series = box.call(
                "metric_series", {"metric": metric, "start": onset, "end": now, "slice": place}
            )
            rows = [r for r in series.rows if r["expected"]]
            if not rows:
                return None
            last = rows[-1]
            ratios[metric] = last["observed"] / last["expected"]
            cited += self._cite(series, last["key"], "observed") + self._cite(
                series, last["key"], "expected"
            )
        if (
            ratios["sessions"] < BOT_SESSIONS
            or ratios["checkout_conversion"] > 1 / BOT_SESSIONS * 1.2
        ):
            return None
        # The target must be seen in the data, not only named by the alert.
        for dimension, other in (("channel", "country"), ("country", "channel")):
            split = box.call(
                "breakdown",
                {
                    "metric": "sessions",
                    "dimension": dimension,
                    "start": onset,
                    "end": now,
                    "slice": {other: where[other]},
                },
            )
            if self._concentrated(split, 1) != where[dimension]:
                return None
            cited += self._cite(split, where[dimension], "excess")
        return cited


def _key(where: Mapping[str, str]) -> str:
    return (
        ", ".join(f"{dim}={value}" for dim, value in sorted(where.items())) or "the whole business"
    )
