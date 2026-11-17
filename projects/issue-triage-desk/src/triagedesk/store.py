"""The local store: issues as first seen, their word index, vectors and timelines.

One SQLite file under `var/`, git-ignored. It holds third-party text, so nothing in
it is committed. Title and body are written once, when an issue is first seen, and
never overwritten: a later refresh updates labels and state only.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from .models import Issue, TimelineEvent
from .text import search_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    number INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    labels_first_seen TEXT NOT NULL,
    state_first_seen TEXT NOT NULL,
    labels TEXT NOT NULL,
    state TEXT NOT NULL,
    state_reason TEXT,
    closed_at TEXT,
    updated_at TEXT,
    refreshed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS issues_created ON issues (created_at);
CREATE VIRTUAL TABLE IF NOT EXISTS issue_fts USING fts5(text, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS vectors (
    number INTEGER, model TEXT, vector BLOB, PRIMARY KEY (number, model)
);
CREATE TABLE IF NOT EXISTS timelines (number INTEGER PRIMARY KEY, fetched_at TEXT, events TEXT);
CREATE TABLE IF NOT EXISTS notes (
    number INTEGER, system_version TEXT, note TEXT, PRIMARY KEY (number, system_version)
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
"""


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).isoformat(timespec="seconds") if moment else None


def _row_to_issue(row: sqlite3.Row) -> Issue:
    return Issue(
        number=row["number"],
        title=row["title"],
        body=row["body"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        closed_at=row["closed_at"],
        state=row["state"],
        state_reason=row["state_reason"],
        labels=json.loads(row["labels"]),
    )


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Wait rather than fail when a background fetch is writing at the same moment.
        self._db = sqlite3.connect(str(path), timeout=60)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    # -- issues --------------------------------------------------------------------

    def add_issues(self, issues: Iterable[Issue], seen_at: datetime | None = None) -> int:
        """Store new issues and refresh labels and state of known ones. Returns new ones."""
        seen = _iso(seen_at or datetime.now(UTC))
        new = 0
        for issue in issues:
            labels = json.dumps(sorted(issue.labels))
            known = self._db.execute(
                "SELECT 1 FROM issues WHERE number = ?", (issue.number,)
            ).fetchone()
            if known:
                self._db.execute(
                    "UPDATE issues SET labels = ?, state = ?, state_reason = ?, closed_at = ?, "
                    "updated_at = ?, refreshed_at = ? WHERE number = ?",
                    (labels, issue.state, issue.state_reason, _iso(issue.closed_at),
                     _iso(issue.updated_at), seen, issue.number),
                )  # fmt: skip
                continue
            new += 1
            self._db.execute(
                "INSERT INTO issues VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (issue.number, _iso(issue.created_at), issue.title, issue.body, seen, labels,
                 issue.state, labels, issue.state, issue.state_reason, _iso(issue.closed_at),
                 _iso(issue.updated_at), seen),
            )  # fmt: skip
            self._db.execute(
                "INSERT INTO issue_fts (rowid, text) VALUES (?, ?)",
                (issue.number, search_text(issue.title, issue.body)),
            )
        self._db.commit()
        return new

    def issue(self, number: int) -> Issue | None:
        row = self._db.execute("SELECT * FROM issues WHERE number = ?", (number,)).fetchone()
        return _row_to_issue(row) if row else None

    def issues(self, start: datetime | None = None, end: datetime | None = None) -> list[Issue]:
        """Issues created in [start, end), oldest first."""
        clauses, args = [], []
        if start is not None:
            clauses.append("created_at >= ?")
            args.append(_iso(start))
        if end is not None:
            clauses.append("created_at < ?")
            args.append(_iso(end))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._db.execute(
            f"SELECT * FROM issues {where} ORDER BY created_at, number", args
        ).fetchall()
        return [_row_to_issue(row) for row in rows]

    def numbers(self) -> set[int]:
        return {row[0] for row in self._db.execute("SELECT number FROM issues")}

    def count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM issues").fetchone()[0]

    def span(self) -> tuple[datetime | None, datetime | None]:
        row = self._db.execute("SELECT MIN(created_at), MAX(created_at) FROM issues").fetchone()
        return (
            datetime.fromisoformat(row[0]) if row[0] else None,
            datetime.fromisoformat(row[1]) if row[1] else None,
        )

    def first_seen(self, number: int) -> tuple[datetime, list[str], str] | None:
        """When an issue was first seen and what the tracker showed then."""
        row = self._db.execute(
            "SELECT first_seen_at, labels_first_seen, state_first_seen FROM issues "
            "WHERE number = ?",
            (number,),
        ).fetchone()
        if not row:
            return None
        return datetime.fromisoformat(row[0]), json.loads(row[1]), row[2]

    def refreshed_at(self, number: int) -> datetime | None:
        row = self._db.execute(
            "SELECT refreshed_at FROM issues WHERE number = ?", (number,)
        ).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    # -- word search ---------------------------------------------------------------

    def search_words(self, query: str, before: datetime, limit: int) -> list[tuple[int, float]]:
        """Issues created before `before` that match an FTS5 query, best first."""
        if not query:
            return []
        rows = self._db.execute(
            "SELECT issues.number, bm25(issue_fts) AS rank FROM issue_fts "
            "JOIN issues ON issues.number = issue_fts.rowid "
            "WHERE issue_fts MATCH ? AND issues.created_at < ? ORDER BY rank LIMIT ?",
            (query, _iso(before), limit),
        ).fetchall()
        # bm25() is smaller for a better match; flip it so that larger is better.
        return [(row[0], -float(row[1])) for row in rows]

    # -- vectors -------------------------------------------------------------------

    def missing_vectors(self, model: str) -> list[int]:
        rows = self._db.execute(
            "SELECT issues.number FROM issues LEFT JOIN vectors "
            "ON vectors.number = issues.number AND vectors.model = ? "
            "WHERE vectors.number IS NULL ORDER BY issues.created_at",
            (model,),
        ).fetchall()
        return [row[0] for row in rows]

    def add_vectors(self, model: str, numbers: list[int], vectors: np.ndarray) -> None:
        self._db.executemany(
            "INSERT OR REPLACE INTO vectors VALUES (?, ?, ?)",
            [
                (number, model, vector.astype(np.float32).tobytes())
                for number, vector in zip(numbers, vectors, strict=True)
            ],
        )
        self._db.commit()

    def vectors(self, model: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Issue numbers, creation times (epoch seconds) and vectors, oldest first."""
        rows = self._db.execute(
            "SELECT issues.number, issues.created_at, vectors.vector FROM vectors "
            "JOIN issues ON issues.number = vectors.number WHERE vectors.model = ? "
            "ORDER BY issues.created_at, issues.number",
            (model,),
        ).fetchall()
        if not rows:
            empty = np.zeros(0, dtype=np.int64)
            return empty, np.zeros(0, dtype=np.float64), np.zeros((0, 0), dtype=np.float32)
        numbers = np.array([row[0] for row in rows], dtype=np.int64)
        times = np.array([datetime.fromisoformat(row[1]).timestamp() for row in rows])
        matrix = np.vstack([np.frombuffer(row[2], dtype=np.float32) for row in rows])
        return numbers, times, matrix

    # -- timelines, notes, state ---------------------------------------------------

    def set_timeline(self, number: int, events: list[TimelineEvent]) -> None:
        payload = json.dumps([e.model_dump(mode="json", exclude_defaults=True) for e in events])
        self._db.execute(
            "INSERT OR REPLACE INTO timelines VALUES (?, ?, ?)",
            (number, _iso(datetime.now(UTC)), payload),
        )
        self._db.commit()

    def timeline(self, number: int) -> list[TimelineEvent] | None:
        row = self._db.execute(
            "SELECT events FROM timelines WHERE number = ?", (number,)
        ).fetchone()
        if not row:
            return None
        return [TimelineEvent.model_validate(e) for e in json.loads(row[0])]

    def timeline_numbers(self) -> set[int]:
        return {row[0] for row in self._db.execute("SELECT number FROM timelines")}

    def set_note(self, number: int, system_version: str, note: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO notes VALUES (?, ?, ?)", (number, system_version, note)
        )
        self._db.commit()

    def note(self, number: int, system_version: str | None = None) -> str | None:
        if system_version is None:
            row = self._db.execute(
                "SELECT note FROM notes WHERE number = ? ORDER BY rowid DESC", (number,)
            ).fetchone()
        else:
            row = self._db.execute(
                "SELECT note FROM notes WHERE number = ? AND system_version = ?",
                (number, system_version),
            ).fetchone()
        return row[0] if row else None

    def get_state(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, value))
        self._db.commit()
