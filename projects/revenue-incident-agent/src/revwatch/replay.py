"""From alerts to a queue: investigate each alert, check the finding, store it."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from revwatch.guardrails import Assessment, assess
from revwatch.investigate.base import Case, Investigation, Investigator, StopRun, no_finding
from revwatch.policy import ReviewPolicy
from revwatch.store import RunInfo, Store
from revwatch.tools import Toolbox
from revwatch.warehouse import Warehouse


@dataclass(frozen=True)
class Handled:
    """One alert after investigation and checks."""

    case: Case
    investigation: Investigation
    assessment: Assessment


def handle(
    world: Path,
    cases: Sequence[Case],
    investigator: Investigator,
    policy: ReviewPolicy | None = None,
    concurrency: int = 1,
    progress: Callable[[str], None] | None = None,
) -> list[Handled]:
    """Investigate and check every case, `concurrency` at a time. Results keep the input order.

    Each case reads the world as it was when its alert was raised. An
    investigation that fails is not dropped: its incident is queued with the
    reason and no finding. Only `StopRun` ends the run.
    """
    policy = policy or ReviewPolicy()
    done = 0

    def one(case: Case) -> Handled:
        nonlocal done
        # One connection per investigation: they run on several threads.
        with Warehouse(world) as warehouse:
            try:
                investigation = investigator.investigate(case, Toolbox(warehouse, case.now))
            except StopRun:
                raise
            except Exception as exc:  # no incident is dropped because its investigation broke
                investigation = Investigation(
                    no_finding("The investigation failed; see the reason."),
                    flags=["INVESTIGATION_ERROR"],
                    detail=f"{type(exc).__name__}: {exc}",
                )
            assessment = assess(case, investigation, Toolbox(warehouse, case.now), policy)
        done += 1
        if progress is not None:
            progress(
                f"{investigator.name}: investigated {done} of {len(cases)} "
                f"({case.incident_id}: {investigation.finding.cause})"
            )
        return Handled(case, investigation, assessment)

    if concurrency <= 1:
        return [one(case) for case in cases]
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        return list(pool.map(one, cases))


def queue(
    store: Store,
    world: Path,
    detector: str,
    investigator: str,
    handled: Sequence[Handled],
    policy: ReviewPolicy | None = None,
) -> tuple[int, int]:
    """Write the handled alerts to the store. Returns (stored, left alone because decided)."""
    with Warehouse(world) as warehouse:
        run = RunInfo(
            warehouse.world_hash, detector, investigator, asdict(policy or ReviewPolicy())
        )
        stored = [
            store.record(
                run,
                item.case,
                warehouse.hour(item.case.alert_tick),
                item.investigation,
                item.assessment,
            )
            for item in handled
        ]
    return sum(stored), len(stored) - sum(stored)
