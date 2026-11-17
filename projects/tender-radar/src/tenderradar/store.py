"""Local SQLite store of raw feed records, parsed on the way out.

Raw records are kept as received, so a parser fix never needs a second download. The
database is local working state and is git-ignored; `tenderradar backfill` rebuilds it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import date
from pathlib import Path

from .models import Award, Notice
from .notices import ParseError, parse_award, parse_notice

COMPETITION = "competition"
AWARD = "award"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw (
    publication_number TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    publication_date TEXT NOT NULL,
    procedure_id TEXT,
    json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS raw_kind_date ON raw (kind, publication_date);
CREATE INDEX IF NOT EXISTS raw_procedure ON raw (procedure_id);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # Wait rather than fail if a download is writing while a report is being read.
        self._db = sqlite3.connect(str(path), timeout=60)
        self._db.executescript(_SCHEMA)
        self.parse_failures = 0

    def close(self) -> None:
        self._db.close()

    # --- writing

    def upsert_raw(self, kind: str, records: Iterable[dict]) -> int:
        """Store records as received. A record seen again replaces itself; returns new rows."""
        before = self.count(kind)
        rows = []
        for record in records:
            number = record.get("publication-number")
            published = record.get("publication-date")
            if not number or not isinstance(published, str):
                self.parse_failures += 1
                continue
            # The feed sends `links` (4.5 KB of document URLs) whether asked or not.
            # The notice URL is built from the publication number, so it is not kept.
            slim = {key: value for key, value in record.items() if key != "links"}
            rows.append(
                (
                    str(number),
                    kind,
                    published[:10],
                    record.get("procedure-identifier"),
                    json.dumps(slim, ensure_ascii=False),
                )
            )
        self._db.executemany(
            "INSERT INTO raw (publication_number, kind, publication_date, procedure_id, json) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(publication_number) DO UPDATE SET "
            "kind=excluded.kind, publication_date=excluded.publication_date, "
            "procedure_id=excluded.procedure_id, json=excluded.json",
            rows,
        )
        self._db.commit()
        return self.count(kind) - before

    def set_state(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._db.commit()

    def get_state(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    # --- reading

    def count(self, kind: str, start: date | None = None, end: date | None = None) -> int:
        sql, args = "SELECT COUNT(*) FROM raw WHERE kind=?", [kind]
        if start:
            sql, args = sql + " AND publication_date>=?", [*args, start.isoformat()]
        if end:
            sql, args = sql + " AND publication_date<=?", [*args, end.isoformat()]
        return self._db.execute(sql, args).fetchone()[0]

    def _raw(self, kind: str, start: date, end: date) -> Iterator[dict]:
        cursor = self._db.execute(
            "SELECT json FROM raw WHERE kind=? AND publication_date>=? AND publication_date<=? "
            "ORDER BY publication_date, publication_number",
            (kind, start.isoformat(), end.isoformat()),
        )
        for (payload,) in cursor:
            yield json.loads(payload)

    def raw_records(self, kind: str, start: date, end: date) -> list[dict]:
        return list(self._raw(kind, start, end))

    def notices(self, start: date, end: date) -> list[Notice]:
        """Competition notices published in the range, oldest first. Bad records are counted."""
        out = []
        for raw in self._raw(COMPETITION, start, end):
            try:
                out.append(parse_notice(raw))
            except ParseError:
                self.parse_failures += 1
        return out

    def awards(self, start: date, end: date) -> list[Award]:
        out = []
        for raw in self._raw(AWARD, start, end):
            try:
                out.append(parse_award(raw))
            except ParseError:
                self.parse_failures += 1
        return out

    def first_publication(self, before: date | None = None) -> dict[str, date]:
        """For each procedure, the day its first competition notice appeared.

        A procedure can have several competition notices (corrections, new versions).
        The day it was first published is the day a bid desk could first have seen it.
        """
        sql = "SELECT procedure_id, MIN(publication_date) FROM raw WHERE kind=?"
        args: list = [COMPETITION]
        if before:
            sql, args = sql + " AND publication_date<?", [*args, before.isoformat()]
        cursor = self._db.execute(sql + " GROUP BY procedure_id", args)
        return {pid: date.fromisoformat(day) for pid, day in cursor if pid}

    def new_procedures(self, start: date, end: date) -> list[Notice]:
        """Notices that are the first publication of their procedure, in the range."""
        seen_before = self.first_publication(before=start)
        first: dict[str, Notice] = {}
        for notice in self.notices(start, end):
            if notice.procedure_id in seen_before:
                continue
            first.setdefault(notice.procedure_id, notice)
        return list(first.values())

    def days(self, kind: str, start: date, end: date) -> list[date]:
        cursor = self._db.execute(
            "SELECT DISTINCT publication_date FROM raw WHERE kind=? AND publication_date>=? "
            "AND publication_date<=? ORDER BY publication_date",
            (kind, start.isoformat(), end.isoformat()),
        )
        return [date.fromisoformat(day) for (day,) in cursor]

    def latest_day(self, kind: str) -> date | None:
        row = self._db.execute(
            "SELECT MAX(publication_date) FROM raw WHERE kind=?", (kind,)
        ).fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None
