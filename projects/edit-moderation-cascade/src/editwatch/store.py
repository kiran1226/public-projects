"""The local store: edits, what each one changed, and Wikimedia's own scores.

One SQLite file under `var/`, git-ignored. Edit summaries and page text are other
people's writing under CC BY-SA; they are kept here to work with and never committed.
No account name is stored anywhere.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from .models import Change, Edit

SCHEMA = """
CREATE TABLE IF NOT EXISTS edits (
    rev_id INTEGER PRIMARY KEY,
    parent_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    minor INTEGER, bot INTEGER, temp INTEGER,
    old_len INTEGER, new_len INTEGER,
    comment TEXT NOT NULL,
    tags TEXT NOT NULL,
    reverted INTEGER,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS edits_time ON edits (timestamp);
CREATE TABLE IF NOT EXISTS changes (rev_id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS baseline (rev_id INTEGER PRIMARY KEY, risk REAL);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
"""


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _edit(row: sqlite3.Row) -> Edit:
    return Edit(
        rev_id=row["rev_id"], parent_id=row["parent_id"], title=row["title"],
        timestamp=row["timestamp"], minor=bool(row["minor"]), bot=bool(row["bot"]),
        temp=bool(row["temp"]), old_len=row["old_len"], new_len=row["new_len"],
        comment=row["comment"],
    )  # fmt: skip


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), timeout=60)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    # -- edits ---------------------------------------------------------------------

    def add_edits(
        self,
        edits: Iterable[tuple[Edit, list[str]]],
        *,
        source: str,
        reverted_tag: str | None = None,
    ) -> int:
        """Store edits that are new. With `reverted_tag`, the tags decide the outcome."""
        new = 0
        for edit, tags in edits:
            reverted = None if reverted_tag is None else int(reverted_tag in tags)
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO edits VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (edit.rev_id, edit.parent_id, edit.title, _iso(edit.timestamp), int(edit.minor),
                 int(edit.bot), int(edit.temp), edit.old_len, edit.new_len, edit.comment,
                 json.dumps(tags), reverted, source),
            )  # fmt: skip
            new += cursor.rowcount
        self._db.commit()
        return new

    def edits(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        source: str | None = None,
        people_only: bool = True,
    ) -> list[Edit]:
        """Edits made in [start, end), oldest first."""
        clauses, args = [], []
        if start is not None:
            clauses.append("timestamp >= ?")
            args.append(_iso(start))
        if end is not None:
            clauses.append("timestamp < ?")
            args.append(_iso(end))
        if source is not None:
            clauses.append("source = ?")
            args.append(source)
        if people_only:
            clauses.append("bot = 0")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._db.execute(
            f"SELECT * FROM edits {where} ORDER BY timestamp, rev_id", args
        ).fetchall()
        return [_edit(row) for row in rows]

    def edit(self, rev_id: int) -> Edit | None:
        row = self._db.execute("SELECT * FROM edits WHERE rev_id = ?", (rev_id,)).fetchone()
        return _edit(row) if row else None

    def reverted(self) -> dict[int, bool]:
        """The outcome of every edit that has one."""
        rows = self._db.execute("SELECT rev_id, reverted FROM edits WHERE reverted IS NOT NULL")
        return {row[0]: bool(row[1]) for row in rows}

    def tags(self, rev_id: int) -> list[str]:
        row = self._db.execute("SELECT tags FROM edits WHERE rev_id = ?", (rev_id,)).fetchone()
        return json.loads(row[0]) if row else []

    def count(self, source: str | None = None) -> int:
        if source is None:
            return self._db.execute("SELECT COUNT(*) FROM edits").fetchone()[0]
        return self._db.execute(
            "SELECT COUNT(*) FROM edits WHERE source = ?", (source,)
        ).fetchone()[0]

    # -- changes, baseline, state ----------------------------------------------------

    def set_change(self, change: Change) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO changes VALUES (?, ?)",
            (change.rev_id, change.model_dump_json()),
        )
        self._db.commit()

    def change(self, rev_id: int) -> Change | None:
        row = self._db.execute("SELECT payload FROM changes WHERE rev_id = ?", (rev_id,)).fetchone()
        return Change.model_validate_json(row[0]) if row else None

    def change_ids(self) -> set[int]:
        return {row[0] for row in self._db.execute("SELECT rev_id FROM changes")}

    def set_baseline(self, rev_id: int, risk: float | None) -> None:
        self._db.execute("INSERT OR REPLACE INTO baseline VALUES (?, ?)", (rev_id, risk))
        self._db.commit()

    def baseline(self) -> dict[int, float | None]:
        return {row[0]: row[1] for row in self._db.execute("SELECT rev_id, risk FROM baseline")}

    def get_state(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, value))
        self._db.commit()
