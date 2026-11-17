"""The incident queue: what was found, what was decided, what was done. SQLite.

Every incident is stored with what produced it (world, detector, investigator,
model, policy), so a number in the queue can be traced back. The event log is
append-only. A reviewer's decision is never overwritten: running the replay
again logs the re-run and leaves decided incidents as they are.

Incident states:
    open               waiting for a reviewer
    approved           cause confirmed; nothing to execute
    action_pending     approved; the admin call has not been confirmed yet
    mitigated          the admin call succeeded
    action_failed      approved; the admin call failed (it can be retried)
    corrected          the reviewer named a different cause; no action
    rejected           not an incident
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from revwatch.findings import Finding
from revwatch.guardrails import Assessment
from revwatch.investigate.base import Case, Investigation
from revwatch.scenarios import Cause, Mitigation

Status = Literal[
    "open", "approved", "action_pending", "mitigated", "action_failed", "corrected", "rejected"
]
DECIDED = ("approved", "action_pending", "mitigated", "action_failed", "corrected", "rejected")
# A reviewer said these were real: their hours leave the detectors' history.
CONFIRMED = ("approved", "action_pending", "mitigated", "action_failed", "corrected")

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    world_hash TEXT NOT NULL,
    detector TEXT NOT NULL,
    investigator TEXT NOT NULL,
    model TEXT NOT NULL,
    policy TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    alert_tick INTEGER NOT NULL,
    metric TEXT NOT NULL,
    slice TEXT NOT NULL,
    direction INTEGER NOT NULL,
    status TEXT NOT NULL,
    severity TEXT NOT NULL,
    impact_eur_per_hour REAL NOT NULL,
    owner TEXT NOT NULL,
    finding TEXT NOT NULL,
    reasons TEXT NOT NULL,
    proposal TEXT,
    trace TEXT NOT NULL,
    evidence_ok INTEGER NOT NULL,
    evidence_total INTEGER NOT NULL,
    tool_calls INTEGER NOT NULL,
    model_calls INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    model_seconds REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
    decision TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    note TEXT NOT NULL,
    corrected_cause TEXT,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
    idempotency_key TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    incident_id TEXT,
    kind TEXT NOT NULL,
    data TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class RunInfo:
    """What produced an incident. Stored on every row."""

    world_hash: str
    detector: str
    investigator: str
    policy: Mapping[str, Any]


@dataclass(frozen=True)
class Record:
    incident_id: str
    world_hash: str
    detector: str
    investigator: str
    model: str
    opened_at: datetime
    alert_tick: int
    metric: str
    slice: dict[str, str]
    direction: int
    status: Status
    severity: str
    impact_eur_per_hour: float
    owner: str
    finding: Finding
    reasons: list[dict[str, str]]
    proposal: Mitigation | None  # the mitigation that passed every check, if one did
    trace: list[dict[str, Any]]
    evidence_ok: int
    evidence_total: int
    tool_calls: int
    model_calls: int
    cost_usd: float
    model_seconds: float


@dataclass(frozen=True)
class PendingAction:
    idempotency_key: str
    incident_id: str
    action: str
    target: dict[str, str]
    reviewer: str
    status: str = "pending"
    attempts: int = 0
    detail: str = ""


class StoreError(ValueError):
    """The request does not fit the incident's state. The message is for the reviewer."""


class Store:
    def __init__(self, path: Path, clock: Callable[[], datetime] | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._clock = clock or (lambda: datetime.now(UTC))
        self._con = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(SCHEMA)

    def close(self) -> None:
        self._con.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self._con.execute("BEGIN IMMEDIATE")
        try:
            yield self._con
        except BaseException:
            self._con.execute("ROLLBACK")
            raise
        self._con.execute("COMMIT")

    def _now(self) -> str:
        return self._clock().isoformat(timespec="seconds")

    def _log(self, con: sqlite3.Connection, incident_id: str | None, kind: str, data: Any) -> None:
        con.execute(
            "INSERT INTO events (at, incident_id, kind, data) VALUES (?, ?, ?, ?)",
            (self._now(), incident_id, kind, json.dumps(data, ensure_ascii=False, default=str)),
        )

    # --- writing what the replay found -----------------------------------------

    def record(
        self,
        run: RunInfo,
        case: Case,
        opened_at: datetime,
        investigation: Investigation,
        assessment: Assessment,
    ) -> bool:
        """Store an incident with its finding. False if a reviewer has already decided it.

        An undecided incident is replaced by the new run's result.
        """
        finding = investigation.finding
        with self._transaction() as con:
            row = con.execute(
                "SELECT status FROM incidents WHERE incident_id = ?", (case.incident_id,)
            ).fetchone()
            if row is not None and row["status"] in DECIDED:
                self._log(con, case.incident_id, "rerun_skipped", {"status": row["status"]})
                return False
            con.execute("DELETE FROM incidents WHERE incident_id = ?", (case.incident_id,))
            con.execute(
                "INSERT INTO incidents VALUES (" + ", ".join("?" * 25) + ")",
                (
                    case.incident_id,
                    run.world_hash,
                    run.detector,
                    run.investigator,
                    investigation.model,
                    json.dumps(dict(run.policy), sort_keys=True),
                    opened_at.isoformat(),
                    case.alert_tick,
                    case.metric,
                    json.dumps(case.slice.as_dict(), sort_keys=True),
                    case.direction,
                    "open",
                    assessment.severity,
                    assessment.impact_eur_per_hour,
                    assessment.owner,
                    finding.model_dump_json(),
                    json.dumps([asdict(held) for held in assessment.reasons]),
                    assessment.mitigation.model_dump_json() if assessment.mitigation else None,
                    json.dumps([asdict(call) for call in investigation.trace], ensure_ascii=False),
                    sum(check.ok for check in assessment.checks),
                    len(assessment.checks),
                    investigation.tool_calls,
                    investigation.model_calls,
                    investigation.cost_usd,
                    investigation.model_seconds,
                ),
            )
            self._log(
                con,
                case.incident_id,
                "recorded",
                {"cause": finding.cause, "severity": assessment.severity, "held": assessment.codes},
            )
        return True

    # --- reading -----------------------------------------------------------------

    def get(self, incident_id: str) -> Record:
        row = self._con.execute(
            "SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"no incident {incident_id}")
        return self._record(row)

    def incidents(self, status: str | None = None) -> list[Record]:
        """Most urgent first: pages, then tickets, then the rest; larger impact first."""
        rows = self._con.execute(
            "SELECT * FROM incidents WHERE (? IS NULL OR status = ?) ORDER BY "
            "CASE severity WHEN 'page' THEN 0 WHEN 'ticket' THEN 1 "
            "WHEN 'digest' THEN 2 ELSE 3 END, "
            "impact_eur_per_hour DESC, incident_id",
            (status, status),
        ).fetchall()
        return [self._record(row) for row in rows]

    @staticmethod
    def _record(row: sqlite3.Row) -> Record:
        return Record(
            incident_id=row["incident_id"],
            world_hash=row["world_hash"],
            detector=row["detector"],
            investigator=row["investigator"],
            model=row["model"],
            opened_at=datetime.fromisoformat(row["opened_at"]),
            alert_tick=row["alert_tick"],
            metric=row["metric"],
            slice=json.loads(row["slice"]),
            direction=row["direction"],
            status=row["status"],
            severity=row["severity"],
            impact_eur_per_hour=row["impact_eur_per_hour"],
            owner=row["owner"],
            finding=Finding.model_validate_json(row["finding"]),
            reasons=json.loads(row["reasons"]),
            proposal=Mitigation.model_validate_json(row["proposal"]) if row["proposal"] else None,
            trace=json.loads(row["trace"]),
            evidence_ok=row["evidence_ok"],
            evidence_total=row["evidence_total"],
            tool_calls=row["tool_calls"],
            model_calls=row["model_calls"],
            cost_usd=row["cost_usd"],
            model_seconds=row["model_seconds"],
        )

    def events(self, incident_id: str | None = None) -> list[dict[str, Any]]:
        rows = self._con.execute(
            "SELECT * FROM events WHERE (? IS NULL OR incident_id = ?) ORDER BY id",
            (incident_id, incident_id),
        ).fetchall()
        return [{**dict(row), "data": json.loads(row["data"])} for row in rows]

    def decisions(self, incident_id: str) -> list[dict[str, Any]]:
        rows = self._con.execute(
            "SELECT * FROM decisions WHERE incident_id = ? ORDER BY id", (incident_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def confirmed(self, incident_id: str) -> bool:
        """Whether a reviewer said this incident was real."""
        row = self._con.execute(
            "SELECT status FROM incidents WHERE incident_id = ?", (incident_id,)
        ).fetchone()
        return row is not None and row["status"] in CONFIRMED

    # --- decisions ---------------------------------------------------------------

    def _decide(
        self,
        con: sqlite3.Connection,
        incident_id: str,
        decision: str,
        status: Status,
        reviewer: str,
        note: str,
        corrected_cause: str | None = None,
    ) -> None:
        if not reviewer.strip():
            raise StoreError("a decision needs the reviewer's name")
        con.execute(
            "INSERT INTO decisions (incident_id, decision, reviewer, note, corrected_cause, at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (incident_id, decision, reviewer, note, corrected_cause, self._now()),
        )
        con.execute("UPDATE incidents SET status = ? WHERE incident_id = ?", (status, incident_id))
        self._log(con, incident_id, decision, {"reviewer": reviewer, "note": note})

    def approve(self, incident_id: str, reviewer: str, note: str = "") -> PendingAction | None:
        """Confirm the cause. Returns the action to execute, if the incident carries a proposal.

        The approval and the pending action are written in one transaction, before
        anything is sent. Approving again returns the same action while it has not
        succeeded, and None once it has: one approval, one call.
        """
        with self._transaction() as con:
            record = self.get(incident_id)
            if record.status in ("corrected", "rejected", "approved", "mitigated"):
                self._log(con, incident_id, "approve_ignored", {"status": record.status})
                return None
            if record.status in ("action_pending", "action_failed"):
                self._log(con, incident_id, "approve_repeated", {"reviewer": reviewer})
                return self.action_for(incident_id)
            if record.proposal is None:
                self._decide(con, incident_id, "approve", "approved", reviewer, note)
                return None
            key = idempotency_key(incident_id, record.proposal)
            con.execute(
                "INSERT INTO actions "
                "(idempotency_key, incident_id, action, target, reviewer, status) "
                "VALUES (?, ?, ?, ?, ?, 'pending')",
                (
                    key,
                    incident_id,
                    record.proposal.action,
                    json.dumps(record.proposal.target, sort_keys=True),
                    reviewer,
                ),
            )
            self._decide(con, incident_id, "approve", "action_pending", reviewer, note)
        return self.action_for(incident_id)

    def correct(self, incident_id: str, reviewer: str, cause: Cause, note: str = "") -> None:
        """The reviewer names the cause. A correction never triggers an action."""
        with self._transaction() as con:
            self._open_only(incident_id)
            self._decide(con, incident_id, "correct", "corrected", reviewer, note, cause)

    def reject(self, incident_id: str, reviewer: str, note: str = "") -> None:
        with self._transaction() as con:
            self._open_only(incident_id)
            self._decide(con, incident_id, "reject", "rejected", reviewer, note)

    def _open_only(self, incident_id: str) -> None:
        status = self.get(incident_id).status
        if status != "open":
            raise StoreError(f"{incident_id} is already {status}")

    # --- actions -----------------------------------------------------------------

    def action_for(self, incident_id: str) -> PendingAction | None:
        row = self._con.execute(
            "SELECT * FROM actions WHERE incident_id = ?", (incident_id,)
        ).fetchone()
        if row is None:
            return None
        return PendingAction(
            row["idempotency_key"],
            row["incident_id"],
            row["action"],
            json.loads(row["target"]),
            row["reviewer"],
            row["status"],
            row["attempts"],
            row["detail"],
        )

    def log_attempt(self, action: PendingAction, attempt: dict[str, Any]) -> None:
        """Append one admin call to the event log: what was sent and what came back."""
        with self._transaction() as con:
            con.execute(
                "UPDATE actions SET attempts = attempts + 1 WHERE idempotency_key = ?",
                (action.idempotency_key,),
            )
            self._log(con, action.incident_id, "admin_call", attempt)

    def finish_action(self, action: PendingAction, succeeded: bool, detail: str) -> None:
        with self._transaction() as con:
            con.execute(
                "UPDATE actions SET status = ?, detail = ? WHERE idempotency_key = ?",
                ("succeeded" if succeeded else "failed", detail, action.idempotency_key),
            )
            con.execute(
                "UPDATE incidents SET status = ? WHERE incident_id = ?",
                ("mitigated" if succeeded else "action_failed", action.incident_id),
            )
            self._log(
                con,
                action.incident_id,
                "action_succeeded" if succeeded else "action_failed",
                {"detail": detail},
            )

    # --- numbers and feedback ------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        records = self.incidents()
        decided = [r for r in records if r.status in DECIDED]
        overridden = [r for r in decided if r.status in ("corrected", "rejected")]

        def count(values: list[str]) -> dict[str, int]:
            return {value: values.count(value) for value in sorted(set(values))}

        return {
            "incidents": len(records),
            "by_status": count([r.status for r in records]),
            "by_severity": count([r.severity for r in records]),
            "by_cause": count([r.finding.cause for r in records]),
            "held": count([reason["code"] for r in records for reason in r.reasons]),
            "with_proposal": sum(r.proposal is not None for r in records),
            "decided": len(decided),
            "override_rate": len(overridden) / len(decided) if decided else None,
            "cost_usd": sum(r.cost_usd for r in records),
            "tool_calls": sum(r.tool_calls for r in records),
        }

    def corrections(self) -> list[dict[str, Any]]:
        """Corrected incidents as labelled entries: the reviewer's cause is the label."""
        rows = self._con.execute(
            "SELECT d.incident_id, d.corrected_cause, d.reviewer, d.note FROM decisions d "
            "WHERE d.decision = 'correct' ORDER BY d.id"
        ).fetchall()
        out = []
        for row in rows:
            record = self.get(row["incident_id"])
            out.append(
                {
                    "id": f"review-{record.incident_id}",
                    "kind": "incident",
                    "cause": row["corrected_cause"],
                    "start": record.opened_at.isoformat(timespec="minutes"),
                    "slice": record.slice,
                    "metric": record.metric,
                    "world_hash": record.world_hash,
                    "reviewer": row["reviewer"],
                    "note": row["note"],
                    "found_by_system": record.finding.cause,
                }
            )
        return out


def idempotency_key(incident_id: str, mitigation: Mitigation) -> str:
    """The same incident, action and target always give the same key."""
    target = ",".join(f"{k}={v}" for k, v in sorted(mitigation.target.items()))
    return f"{incident_id}:{mitigation.action}:{target}"
