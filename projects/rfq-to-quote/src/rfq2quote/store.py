"""Review queue and audit trail.

Every pipeline result is stored. A person decides the held ones. Every action is
appended to an event log, and a reviewer's decision is never overwritten by
re-processing the same request.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from .models import PipelineResult
from .pipeline import DATA_DIR, PROJECT_ROOT

DEFAULT_DB = PROJECT_ROOT / "var" / "review.db"
DECISIONS = ("approved", "corrected", "rejected")


class ReviewStore:
    def __init__(self, path: Path | None = None):
        path = path or Path(os.environ.get("RFQ2QUOTE_REVIEW_DB", DEFAULT_DB))
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            "CREATE TABLE IF NOT EXISTS quotes (rfq_id TEXT PRIMARY KEY, doc_sha256 TEXT, "
            "created_at TEXT, status TEXT, result_json TEXT, final_json TEXT, reviewer TEXT, "
            "reviewer_note TEXT, decided_at TEXT);"
            "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, "
            "rfq_id TEXT, action TEXT, actor TEXT, detail TEXT);"
        )
        self.conn.commit()

    def _log(self, rfq_id: str, action: str, actor: str, detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO events (at, rfq_id, action, actor, detail) VALUES (?,?,?,?,?)",
            (_now(), rfq_id, action, actor, detail),
        )

    def _own_entry(self, result: PipelineResult) -> PipelineResult:
        """A different document under a name already in the queue is stored under its own id.

        The id comes from the file name, so two files can share it. Without this the
        second one would replace a pending quote or hide behind an earlier decision.
        """
        sha = result.provenance.doc_sha256
        row = self.conn.execute(
            "SELECT doc_sha256 FROM quotes WHERE rfq_id = ?", (result.rfq_id,)
        ).fetchone()
        if not (row and sha and row["doc_sha256"] and row["doc_sha256"] != sha):
            return result
        own_id = f"{result.rfq_id}-{sha[:8]}"
        self._log(result.rfq_id, "name_collision", "agent", f"stored as {own_id}")
        return result.model_copy(update={"rfq_id": own_id})

    def save(self, result: PipelineResult) -> str:
        """Store a result and return its status. A reviewer's decision is kept, not replaced."""
        result = self._own_entry(result)
        existing = self.conn.execute(
            "SELECT status FROM quotes WHERE rfq_id = ?", (result.rfq_id,)
        ).fetchone()
        if existing and existing["status"] in DECISIONS:
            self._log(result.rfq_id, "reprocess_skipped", "agent", f"kept {existing['status']}")
            self.conn.commit()
            return existing["status"]
        status = "auto_approved" if result.route == "auto_approve" else "pending"
        self.conn.execute(
            "INSERT OR REPLACE INTO quotes VALUES (?,?,?,?,?,NULL,NULL,NULL,NULL)",
            (
                result.rfq_id,
                result.provenance.doc_sha256,
                _now(),
                status,
                result.model_dump_json(),
            ),
        )
        reasons = ",".join(sorted({f.code for f in result.flags if f.severity == "review"}))
        self._log(result.rfq_id, status, "agent", reasons)
        self.conn.commit()
        return status

    def list(self, status: str | None = None) -> list[tuple[str, str, PipelineResult]]:
        sql = "SELECT rfq_id, status, COALESCE(final_json, result_json) AS payload FROM quotes"
        args: tuple = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        rows = self.conn.execute(sql + " ORDER BY rfq_id", args).fetchall()
        return [
            (r["rfq_id"], r["status"], PipelineResult.model_validate_json(r["payload"]))
            for r in rows
        ]

    def decide(
        self,
        rfq_id: str,
        status: str,
        reviewer: str,
        note: str = "",
        final: PipelineResult | None = None,
    ) -> None:
        if status not in DECISIONS:
            raise ValueError(f"Not a reviewer decision: {status!r}")
        updated = self.conn.execute(
            "UPDATE quotes SET status = ?, reviewer = ?, reviewer_note = ?, final_json = ?, "
            "decided_at = ? WHERE rfq_id = ?",
            (status, reviewer, note, final.model_dump_json() if final else None, _now(), rfq_id),
        )
        if updated.rowcount == 0:
            raise KeyError(f"No quote with id {rfq_id!r}")
        self._log(rfq_id, status, reviewer, note)
        self.conn.commit()

    def events(self, rfq_id: str | None = None) -> list[dict]:
        sql, args = "SELECT at, rfq_id, action, actor, detail FROM events", ()
        if rfq_id:
            sql, args = sql + " WHERE rfq_id = ?", (rfq_id,)
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY id", args)]

    def counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) AS n FROM quotes GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    def stats(self) -> dict:
        """The numbers to watch after go-live: volume, hold reasons, overrides, cost."""
        counts = self.counts()
        total = sum(counts.values())
        decided = sum(counts.get(s, 0) for s in DECISIONS)
        reasons: Counter[str] = Counter()
        cost = 0.0
        for row in self.conn.execute("SELECT result_json FROM quotes"):
            result = PipelineResult.model_validate_json(row["result_json"])
            reasons.update({f.code for f in result.flags if f.severity == "review"})
            cost += result.usage.cost_usd
        return {
            "requests": total,
            "by_status": counts,
            "auto_approved_share": counts.get("auto_approved", 0) / total if total else 0.0,
            "reviewed": decided,
            # Share of reviewed quotes a person had to change or throw away.
            "override_rate": (
                (counts.get("corrected", 0) + counts.get("rejected", 0)) / decided
                if decided
                else 0.0
            ),
            "hold_reasons": dict(reasons.most_common()),
            "llm_cost_usd": round(cost, 4),
        }


def save_correction(final: PipelineResult, data_dir: Path = DATA_DIR) -> Path:
    """Write a reviewer-confirmed result as a labelled eval case under data/corrections/.

    Everything in it was shown to and confirmed by the reviewer. If a hand-written
    label already exists for this request, the eval keeps using the hand label.
    """
    source = Path(final.source)
    try:
        source = source.resolve().relative_to(data_dir.resolve())
    except ValueError:
        pass  # document lives outside the dataset; keep the path as given
    case = {
        "id": final.rfq_id,
        "source": str(source),
        "received": final.as_of,
        "hard": "reviewer correction",
        "split": "correction",
        "customer_name": final.rfq.customer_name,
        "customer_email": final.rfq.customer_email,
        "delivery_date": final.rfq.delivery_date,
        "delivery_location": [final.rfq.delivery_location] if final.rfq.delivery_location else [],
        "open_questions": bool(final.rfq.missing_info),
        "items": [
            {
                "text": m.item.description,
                "sku": m.sku,
                "quantity": m.item.quantity,
                "unit": m.item.unit,
            }
            for m in final.matches
        ],
    }
    out = data_dir / "corrections" / f"{final.rfq_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(case, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
