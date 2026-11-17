"""Recompute every number a finding cites. A brief must not contain invented values."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from revwatch.findings import Citation, Finding
from revwatch.tools import Toolbox, ToolResult


@dataclass(frozen=True)
class Check:
    citation: Citation
    ok: bool
    why: str = ""  # empty when ok


def verify(
    finding: Finding, trace: Sequence[ToolResult], toolbox: Toolbox, tolerance: float
) -> list[Check]:
    """One check per citation: the call is in this investigation, and re-running it gives the value.

    The call is run again from its recorded arguments and not read back from the
    trace, so a trace cannot vouch for itself.
    """
    calls = {call.call_id: call for call in trace}
    checks: list[Check] = []
    for citation in finding.evidence:
        earlier = calls.get(citation.call_id)
        if earlier is None:
            checks.append(
                Check(citation, False, f"no tool call {citation.call_id} in this investigation")
            )
            continue
        again = toolbox.rerun(earlier)
        if again.error is not None:
            checks.append(Check(citation, False, f"{citation.call_id} failed: {again.error}"))
            continue
        actual = again.value(citation.row, citation.column)
        if not isinstance(actual, int | float) or isinstance(actual, bool):
            checks.append(
                Check(
                    citation,
                    False,
                    f"{citation.call_id} has no number at {citation.row}/{citation.column}",
                )
            )
            continue
        if abs(citation.value - actual) > tolerance * max(abs(actual), 1e-12):
            checks.append(
                Check(citation, False, f"cited {citation.value:g}, recomputed {actual:g}")
            )
            continue
        checks.append(Check(citation, True))
    return checks
