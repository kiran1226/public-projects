"""Case store: results, the token vault and analyst decisions.

Results hold tokens only. The vault is kept in its own column and is read only to
show a case to the analyst.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .models import CaseResult
from .privacy import Vault

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "var" / "cases.db"


class CaseStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or os.environ.get("AML_AGENT_DB") or DEFAULT_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cases (alert_id TEXT, config TEXT, result TEXT, "
                "vault TEXT, saved_at TEXT, PRIMARY KEY (alert_id, config))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS decisions (alert_id TEXT, config TEXT, "
                "analyst TEXT, decision TEXT, note TEXT, decided_at TEXT)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, result: CaseResult, vault: Vault) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cases VALUES (?, ?, ?, ?, ?)",
                (
                    result.alert_id,
                    result.config,
                    result.model_dump_json(),
                    json.dumps(vault.dump()),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def get(self, alert_id: str, config: str) -> tuple[CaseResult, Vault] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result, vault FROM cases WHERE alert_id = ? AND config = ?",
                (alert_id, config),
            ).fetchone()
        if row is None:
            return None
        return CaseResult.model_validate_json(row["result"]), Vault.load(json.loads(row["vault"]))

    def list(self, config: str | None = None) -> list[CaseResult]:
        sql, params = "SELECT result FROM cases", ()
        if config:
            sql, params = sql + " WHERE config = ?", (config,)
        with self._connect() as conn:
            rows = conn.execute(sql + " ORDER BY alert_id, config", params).fetchall()
        return [CaseResult.model_validate_json(r["result"]) for r in rows]

    def decide(
        self, alert_id: str, config: str, decision: str, note: str = "", analyst: str = "analyst"
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?)",
                (alert_id, config, analyst, decision, note, datetime.now(UTC).isoformat()),
            )

    def decisions(self, alert_id: str, config: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT analyst, decision, note, decided_at FROM decisions "
                "WHERE alert_id = ? AND config = ? ORDER BY rowid",
                (alert_id, config),
            ).fetchall()
        return [dict(r) for r in rows]
