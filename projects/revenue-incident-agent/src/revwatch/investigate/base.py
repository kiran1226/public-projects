"""What every investigator takes and returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from revwatch.findings import Finding
from revwatch.models import Incident, Slice
from revwatch.tools import Toolbox, ToolResult


@dataclass(frozen=True)
class Case:
    """An alert as an investigator gets it: what moved, where, which way, and when."""

    incident_id: str
    metric: str
    slice: Slice
    direction: int
    alert_tick: int  # the hour the alert is about; data up to and including it is complete

    @classmethod
    def of(cls, incident: Incident) -> Case:
        return cls(
            incident.incident_id,
            incident.metric,
            incident.slice,
            incident.direction,
            incident.opened_tick,
        )

    @property
    def now(self) -> int:
        """The first hour an investigation of this alert cannot read."""
        return self.alert_tick + 1


@dataclass
class Investigation:
    """A finding with everything needed to check it and to cost it."""

    finding: Finding
    trace: list[ToolResult] = field(default_factory=list)
    # Reasons the investigator itself reports: BUDGET_EXHAUSTED, INVESTIGATION_ERROR.
    flags: list[str] = field(default_factory=list)
    detail: str = ""  # what went wrong, when a flag is set
    model: str = ""
    model_calls: int = 0
    cost_usd: float = 0.0
    model_seconds: float = 0.0

    @property
    def tool_calls(self) -> int:
        return len(self.trace)


class StopRun(RuntimeError):
    """The whole run cannot go on, for example no recording exists and live calls are off.

    Everything else that goes wrong in one investigation is reported on that
    incident and the run continues.
    """


class Investigator(Protocol):
    name: str

    def investigate(self, case: Case, toolbox: Toolbox) -> Investigation: ...


def no_finding(summary: str) -> Finding:
    """The finding of an investigation that could not conclude: no cause, nothing proposed."""
    return Finding(cause="unknown", confidence="low", summary=summary)
