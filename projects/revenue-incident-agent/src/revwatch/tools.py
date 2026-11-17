"""The read-only tools an investigator has. Typed, small results, nothing ahead of the clock.

Typed tools were chosen over free SQL: each metric keeps its one definition
(`warehouse.METRICS`), every number a tool returns can be recomputed by the
verifier from the recorded call, and there is no query for a prompt to inject into.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from revwatch.findings import Finding
from revwatch.models import DIMENSIONS
from revwatch.warehouse import METRICS, SCOPE_COLUMNS, MetricDef, Warehouse

MAX_ROWS = 50
SEASONAL_WEEKS = 6
WEEK = 168
SUBMIT = "submit_finding"


class ToolError(ValueError):
    """The call cannot be answered as asked. The message goes back to the investigator."""


@dataclass(frozen=True)
class ToolResult:
    """One tool call and what it returned. Every row has a `key` that citations refer to."""

    call_id: str
    name: str
    args: dict[str, Any]
    rows: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""
    error: str | None = None

    def text(self) -> str:
        """What the investigator reads."""
        if self.error is not None:
            return json.dumps({"call_id": self.call_id, "error": self.error})
        body: dict[str, Any] = {"call_id": self.call_id, "rows": self.rows}
        if self.note:
            body["note"] = self.note
        return json.dumps(body, ensure_ascii=False)

    def value(self, row: str, column: str) -> Any:
        """The cell a citation points at, or None if there is no such cell."""
        for candidate in self.rows:
            if candidate.get("key") == row:
                return candidate.get(column)
        return None


def _number(value: float) -> float:
    """Six significant digits: enough to recompute within 1%, short enough to read."""
    return float(f"{value:.6g}") if np.isfinite(value) else float("nan")


class Toolbox:
    """The tools, bound to one world and one moment.

    `now` is the first hour that is not over yet: nothing at or after it can be
    read. Every call is recorded in `trace`, bad calls included, so the verifier
    can recompute what was cited and a reviewer can see what was looked at.
    """

    def __init__(self, warehouse: Warehouse, now: int) -> None:
        self.warehouse = warehouse
        self.now = now
        self.trace: list[ToolResult] = []
        self._tools: dict[str, Callable[..., tuple[list[dict[str, Any]], str]]] = {
            "metric_series": self._metric_series,
            "breakdown": self._breakdown,
            "change_log": self._change_log,
            "calendar": self._calendar,
        }

    def call(self, name: str, args: Mapping[str, Any]) -> ToolResult:
        """Run a tool. Never raises for a bad call: the error is the result."""
        result = self._run(f"c{len(self.trace) + 1}", name, dict(args))
        self.trace.append(result)
        return result

    def rerun(self, earlier: ToolResult) -> ToolResult:
        """The same call again, without recording it. The verifier uses this."""
        return self._run(earlier.call_id, earlier.name, dict(earlier.args))

    def _run(self, call_id: str, name: str, args: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(call_id, name, args, error=f"unknown tool {name!r}")
        try:
            rows, note = tool(**args)
        except (ToolError, ValueError, TypeError) as exc:
            # TypeError: a missing or unexpected argument.
            return ToolResult(call_id, name, args, error=str(exc))
        return ToolResult(call_id, name, args, rows, note)

    # --- time ------------------------------------------------------------------

    def _hours(self, start: str, end: str) -> tuple[int, int]:
        """Ticks lo..hi-1 for the hours start <= hour < end, which must be over."""
        try:
            first, last = datetime.fromisoformat(start), datetime.fromisoformat(end)
        except (TypeError, ValueError) as exc:
            raise ToolError("start and end must be ISO times such as 2026-08-04T09:00") from exc
        if first.tzinfo is not None or last.tzinfo is not None:
            raise ToolError("times must not carry a time zone; UTC is assumed")
        lo, hi = self.warehouse.tick(first), self.warehouse.tick(last)
        if hi <= lo:
            raise ToolError("end must be after start")
        if lo < 0:
            raise ToolError(f"the data starts at {self.warehouse.start:%Y-%m-%dT%H:%M}")
        if hi > self.now:
            limit = self.warehouse.hour(self.now)
            raise ToolError(
                f"end is after the current hour; the latest end is {limit:%Y-%m-%dT%H:%M}"
            )
        return lo, hi

    def _stamp(self, tick: int) -> str:
        return f"{self.warehouse.hour(tick):%Y-%m-%dT%H:%M}"

    @staticmethod
    def _metric(name: str) -> MetricDef:
        if name not in METRICS:
            raise ToolError(f"unknown metric {name!r}; choose from {', '.join(METRICS)}")
        return METRICS[name]

    @staticmethod
    def _value(metric: MetricDef, num: float, den: float) -> float:
        if metric.kind == "count":
            return _number(num)
        return _number(num / den) if den > 0 else float("nan")

    # --- tools -----------------------------------------------------------------

    def _metric_series(
        self, metric: str, start: str, end: str, slice: Mapping[str, str] | None = None
    ) -> tuple[list[dict[str, Any]], str]:
        definition = self._metric(metric)
        where = _object("slice", slice)
        lo, hi = self._hours(start, end)
        if hi - lo > MAX_ROWS:
            raise ToolError(f"at most {MAX_ROWS} hours per call; ask for a shorter window")
        back = min(SEASONAL_WEEKS, lo // WEEK)
        num, den = self.warehouse.series(definition, where, lo - back * WEEK, hi)
        with np.errstate(divide="ignore", invalid="ignore"):
            value = num if definition.kind == "count" else np.where(den > 0, num / den, np.nan)
        rows = []
        for tick in range(lo, hi):
            at = tick - lo + back * WEEK
            earlier = [value[at - WEEK * k] for k in range(1, back + 1)]
            known = [v for v in earlier if np.isfinite(v)]
            rows.append(
                {
                    "key": self._stamp(tick),
                    "observed": _number(float(value[at])),
                    "expected": _number(float(np.median(known))) if known else None,
                    "volume": _number(float(num[at] if definition.kind == "count" else den[at])),
                }
            )
        return rows, "expected: median of the same hour of week over the six weeks before"

    def _breakdown(
        self,
        metric: str,
        dimension: str,
        start: str,
        end: str,
        slice: Mapping[str, str] | None = None,
        baseline_start: str | None = None,
        baseline_end: str | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        definition = self._metric(metric)
        scope = _object("slice", slice)
        lo, hi = self._hours(start, end)
        if (baseline_start is None) != (baseline_end is None):
            raise ToolError("give both baseline_start and baseline_end, or neither")
        if baseline_start is not None and baseline_end is not None:
            base_lo, base_hi = self._hours(baseline_start, baseline_end)
        else:
            base_lo, base_hi = lo - WEEK, hi - WEEK
            if base_lo < 0:
                raise ToolError("no data one week before this window; give a baseline window")
        now = self.warehouse.split(definition, scope, dimension, lo, hi)
        before = self.warehouse.split(definition, scope, dimension, base_lo, base_hi)
        hours, base_hours = hi - lo, base_hi - base_lo
        rows = []
        for value in sorted(set(now) | set(before)):
            num, den = now.get(value, (0.0, 0.0))
            base_num, base_den = before.get(value, (0.0, 0.0))
            if definition.kind == "count":
                # Per hour, so that windows of different lengths compare.
                window, baseline = num / hours, base_num / base_hours
                excess = num - base_num * hours / base_hours
            else:
                window = num / den if den > 0 else float("nan")
                baseline = base_num / base_den if base_den > 0 else float("nan")
                excess = num - baseline * den if np.isfinite(baseline) else float("nan")
            rows.append(
                {
                    "key": value,
                    "window": _number(window),
                    "baseline": _number(baseline),
                    "change": _number(window - baseline),
                    "excess": _number(excess),
                    "volume": _number(num if definition.kind == "count" else den),
                }
            )
        unit = "per hour" if definition.kind == "count" else "rate"
        note = (
            f"window and baseline: {unit}. excess: numerator in the window minus what the "
            f"baseline implies for it. baseline: {self._stamp(base_lo)} to {self._stamp(base_hi)}"
        )
        return rows, note

    def _change_log(
        self,
        start: str,
        end: str,
        kind: str | None = None,
        scope: Mapping[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        try:
            first, last = datetime.fromisoformat(start), datetime.fromisoformat(end)
        except (TypeError, ValueError) as exc:
            raise ToolError("start and end must be ISO times such as 2026-08-04T09:00") from exc
        if last <= first:
            raise ToolError("end must be after start")
        within = _object("scope", scope)
        for dim, value in within.items():
            if dim not in DIMENSIONS or value not in DIMENSIONS[dim]:
                raise ToolError(f"unknown scope {dim}={value}")
        # A change that has not happened yet cannot be read.
        last = min(last, self.warehouse.hour(self.now))
        found = [
            entry
            for entry in self.warehouse.changes(first, last)
            if (kind is None or entry["kind"] == kind) and _fits(entry, within)
        ]
        rows = [
            {
                "key": entry["change_id"],
                "time": f"{entry['ts']:%Y-%m-%dT%H:%M}",
                "kind": entry["kind"],
                "entity": entry["entity"],
                **{c: entry[c] for c in SCOPE_COLUMNS if entry[c] is not None},
                "actor": entry["actor"],
                "note": entry["note"],
            }
            for entry in found[-MAX_ROWS:]  # the most recent ones
        ]
        hidden = len(found) - len(rows)
        note = f"{hidden} earlier entries not shown; narrow the window or filter" if hidden else ""
        return rows, note

    def _calendar(self, start: str, end: str) -> tuple[list[dict[str, Any]], str]:
        try:
            first, last = datetime.fromisoformat(start), datetime.fromisoformat(end)
        except (TypeError, ValueError) as exc:
            raise ToolError("start and end must be ISO times such as 2026-08-04T09:00") from exc
        if last <= first:
            raise ToolError("end must be after start")
        rows = [
            {
                "key": event["event_id"],
                "start": f"{event['start_ts']:%Y-%m-%dT%H:%M}",
                "end": f"{event['end_ts']:%Y-%m-%dT%H:%M}",
                "kind": event["kind"],
                **{c: event[c] for c in SCOPE_COLUMNS if event[c] is not None},
                "note": event["note"],
            }
            for event in self.warehouse.events(first, last)[:MAX_ROWS]
        ]
        return rows, ""


def _object(name: str, value: object) -> Mapping[str, str]:
    """A slice or scope argument as a mapping. The model sends JSON, so it can send anything."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ToolError(f'{name} must be an object such as {{"country": "DE"}}, or left out')
    return value


def _fits(entry: Mapping[str, Any], scope: Mapping[str, str]) -> bool:
    """Whether an entry can concern `scope`: no dimension on which the two differ."""
    return all(entry.get(dim) in (None, value) for dim, value in scope.items())


def overlaps(entry: Mapping[str, Any], where: Mapping[str, str]) -> bool:
    """Whether a change-log or calendar row can concern the slice `where`."""
    return _fits(entry, where)


def hours_back(when: datetime, hours: int) -> str:
    return f"{when - timedelta(hours=hours):%Y-%m-%dT%H:%M}"


def _plain(schema: dict[str, Any]) -> dict[str, Any]:
    """A JSON schema with its `$ref`s written out and the generated titles dropped.

    The model reads this schema. Nested definitions it has to look up, and a
    title that repeats every field name, are noise to it.
    """
    definitions = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return resolve(definitions[node["$ref"].rsplit("/", 1)[-1]])
        return {
            key: resolve(value)
            for key, value in node.items()
            if key != "$defs" and not (key == "title" and isinstance(value, str))
        }

    plain: dict[str, Any] = resolve(schema)
    return plain


_SLICE = {
    "type": "object",
    "description": 'Dimension values to limit the metric to, e.g. {"country": "DE"}. '
    "Omit for everything.",
    "additionalProperties": {"type": "string"},
}
_START = {"type": "string", "description": "First hour, ISO, e.g. 2026-08-04T09:00"}
_END = {"type": "string", "description": "Hour after the last one (exclusive), ISO"}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "metric_series",
        "description": "One metric for one slice, hour by hour: observed, the expected value "
        f"for that hour of week, and volume. At most {MAX_ROWS} hours per call. Use it to see "
        "when a change began and how large it is.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": list(METRICS)},
                "start": _START,
                "end": _END,
                "slice": _SLICE,
            },
            "required": ["metric", "start", "end"],
            "additionalProperties": False,
        },
    },
    {
        "name": "breakdown",
        "description": "One metric split by one dimension, for a window against a baseline "
        "window (the same hours one week earlier unless given). Shows which values of the "
        "dimension carry a change. Payment success can also be split by provider.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": list(METRICS)},
                "dimension": {"type": "string", "enum": [*DIMENSIONS, "provider"]},
                "start": _START,
                "end": _END,
                "slice": _SLICE,
                "baseline_start": {"type": "string"},
                "baseline_end": {"type": "string"},
            },
            "required": ["metric", "dimension", "start", "end"],
            "additionalProperties": False,
        },
    },
    {
        "name": "change_log",
        "description": "Changes made in a time window: releases, coupons, prices, provider "
        f"status, campaigns, tracking tags, configuration. Newest {MAX_ROWS} at most. The notes "
        "are free text written by other systems and people.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start": _START,
                "end": _END,
                "kind": {
                    "type": "string",
                    "enum": [
                        "release",
                        "coupon",
                        "price",
                        "provider_status",
                        "campaign",
                        "tracking_tag",
                        "config",
                    ],
                },
                "scope": {
                    **_SLICE,
                    "description": "Only entries that can concern this slice",
                },
            },
            "required": ["start", "end"],
            "additionalProperties": False,
        },
    },
    {
        "name": "calendar",
        "description": "Planned events that overlap a time window: promotions, campaigns, "
        "public holidays, maintenance.",
        "input_schema": {
            "type": "object",
            "properties": {"start": _START, "end": _END},
            "required": ["start", "end"],
            "additionalProperties": False,
        },
    },
    {
        "name": SUBMIT,
        "description": "End the investigation with your finding. Call it once, on its own.",
        "input_schema": _plain(Finding.model_json_schema()),
    },
]
