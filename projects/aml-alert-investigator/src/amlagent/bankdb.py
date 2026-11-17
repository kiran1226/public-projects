"""The bank's side of the boundary: synthetic core data in an in-memory SQLite database.

Rows returned here hold real identifiers. Only `privacy.Gateway` may turn them into
something a model sees.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import threading
from datetime import date, timedelta
from pathlib import Path

from .models import Alert, Gold

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

SCHEMA = {
    "customers": "customer_id TEXT PRIMARY KEY, kind TEXT, name TEXT, dob TEXT, nationality TEXT, "
    "country TEXT, city TEXT, address TEXT, email TEXT, phone TEXT, occupation TEXT, "
    "risk_rating TEXT, pep INTEGER, expected_monthly_turnover REAL, onboarded TEXT, kyc_notes TEXT",
    "accounts": "account_id TEXT PRIMARY KEY, customer_id TEXT, iban TEXT, opened TEXT",
    "counterparties": "counterparty_id TEXT PRIMARY KEY, name TEXT, kind TEXT, country TEXT, "
    "iban TEXT, customer_id TEXT, sensitive_category TEXT, business_type TEXT, dob TEXT, "
    "first_seen TEXT",
    "transactions": "txn_id TEXT PRIMARY KEY, account_id TEXT, booked_at TEXT, direction TEXT, "
    "amount REAL, channel TEXT, counterparty_id TEXT, description TEXT",
    "watchlist": "entry_id TEXT PRIMARY KEY, name TEXT, aliases TEXT, kind TEXT, dob TEXT, "
    "country TEXT, list TEXT",
    "media": "media_id TEXT PRIMARY KEY, subject_name TEXT, published TEXT, headline TEXT, "
    "snippet TEXT",
    "prior_alerts": "prior_id TEXT PRIMARY KEY, customer_id TEXT, raised TEXT, rule TEXT, "
    "outcome TEXT, note TEXT",
    "links": "customer_id TEXT, related_customer_id TEXT, link_type TEXT",
}


def name_parts(name: str) -> list[str]:
    """The words of a person's name that identify them on their own."""
    return [word for word in re.findall(r"[^\W\d_]+", name) if len(word) >= 4]


class BankDB:
    def __init__(self, core_dir: Path = DATA_DIR / "core"):
        self._lock = threading.Lock()  # tools run in threads; one connection, one lock
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        for table, columns in SCHEMA.items():
            self._conn.execute(f"CREATE TABLE {table} ({columns})")
            with (core_dir / f"{table}.csv").open(newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                names = reader.fieldnames or []
                marks = ", ".join(f":{n}" for n in names)
                self._conn.executemany(
                    f"INSERT INTO {table} ({', '.join(names)}) VALUES ({marks})", list(reader)
                )
        self._conn.commit()

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    def customer(self, customer_id: str) -> dict | None:
        return self._one("SELECT * FROM customers WHERE customer_id = ?", (customer_id,))

    def account(self, account_id: str) -> dict | None:
        return self._one("SELECT * FROM accounts WHERE account_id = ?", (account_id,))

    def accounts(self, customer_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM accounts WHERE customer_id = ? ORDER BY account_id", (customer_id,)
        )

    def transactions(
        self,
        account_id: str,
        start: date,
        end: date,
        direction: str | None = None,
        channel: str | None = None,
        min_amount: float = 0.0,
    ) -> list[dict]:
        sql = (
            "SELECT * FROM transactions WHERE account_id = ? AND booked_at >= ? "
            "AND booked_at < ? AND amount >= ?"
        )
        params: list = [
            account_id,
            start.isoformat(),
            (end + timedelta(days=1)).isoformat(),
            min_amount,
        ]
        if direction:
            sql += " AND direction = ?"
            params.append(direction)
        if channel:
            sql += " AND channel = ?"
            params.append(channel)
        return self._all(sql + " ORDER BY booked_at, txn_id", tuple(params))

    def transaction(self, txn_id: str) -> dict | None:
        return self._one("SELECT * FROM transactions WHERE txn_id = ?", (txn_id,))

    def counterparty(self, counterparty_id: str) -> dict | None:
        return self._one(
            "SELECT * FROM counterparties WHERE counterparty_id = ?", (counterparty_id,)
        )

    def prior_alerts(self, customer_id: str, before: date) -> list[dict]:
        return self._all(
            "SELECT * FROM prior_alerts WHERE customer_id = ? AND raised < ? ORDER BY raised",
            (customer_id, before.isoformat()),
        )

    def links(self, customer_id: str) -> list[dict]:
        rows = self._all(
            "SELECT related_customer_id AS customer_id, link_type FROM links WHERE customer_id = ? "
            "UNION SELECT customer_id, link_type FROM links WHERE related_customer_id = ?",
            (customer_id, customer_id),
        )
        return sorted(rows, key=lambda r: r["customer_id"])

    def watchlist(self) -> list[dict]:
        return self._all("SELECT * FROM watchlist ORDER BY entry_id")

    def media(self, name: str) -> list[dict]:
        return self._all("SELECT * FROM media WHERE subject_name = ? ORDER BY published", (name,))

    def parties(self) -> list[dict]:
        """Every party with the identifiers that point at it, for the scrubber dictionary."""
        out = []
        for c in self._all("SELECT * FROM customers"):
            out.append(
                {
                    "kind": "CUST",
                    "person": c["kind"] == "retail",
                    "key": c["customer_id"],
                    "name": c["name"],
                    "email": c["email"],
                    "phone": c["phone"],
                    "iban": "",
                }
            )
        for a in self._all(
            "SELECT a.*, c.name FROM accounts a JOIN customers c USING (customer_id)"
        ):
            out.append(
                {
                    "kind": "ACCT",
                    "person": False,
                    "key": a["account_id"],
                    "name": "",
                    "email": "",
                    "phone": "",
                    "iban": a["iban"],
                }
            )
        for p in self._all("SELECT * FROM counterparties"):
            out.append(
                {
                    "kind": "CPTY",
                    "person": p["kind"] == "person",
                    "key": p["counterparty_id"],
                    "name": p["name"],
                    "email": "",
                    "phone": "",
                    "iban": p["iban"],
                }
            )
        return out

    def texts(self) -> list[str]:
        """Every free-text field, for measuring the scrubber."""
        rows = self._all(
            "SELECT description AS t FROM transactions UNION ALL "
            "SELECT kyc_notes FROM customers UNION ALL SELECT note FROM prior_alerts "
            "UNION ALL SELECT headline FROM media UNION ALL SELECT snippet FROM media"
        )
        return [r["t"] for r in rows if r["t"]]

    def identifier_inventory(self) -> set[str]:
        """Every real identifier the bank holds. The watchlist is public and not included."""
        values: set[str] = set()
        for c in self._all("SELECT name, address, email, phone FROM customers"):
            values.update(c.values())
        values.update(r["iban"] for r in self._all("SELECT iban FROM accounts"))
        for p in self._all("SELECT name, iban FROM counterparties"):
            values.update(p.values())
        for party in self.parties():
            if party["person"]:  # a surname on its own identifies a person too
                values.update(name_parts(party["name"]))
        return {v for v in values if v}


def load_alerts(directory: Path = DATA_DIR / "alerts") -> list[Alert]:
    files = sorted(directory.glob("alert-*.json"))
    return [Alert.model_validate_json(p.read_text(encoding="utf-8")) for p in files]


def load_gold(directory: Path = DATA_DIR / "gold") -> dict[str, Gold]:
    golds = [
        Gold.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted(directory.glob("alert-*.json"))
    ]
    return {g.alert_id: g for g in golds}


def load_planted(directory: Path = DATA_DIR / "gold") -> list[str]:
    """Identifiers the generator hid in free text that are in no entity table."""
    path = directory / "planted_identifiers.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
