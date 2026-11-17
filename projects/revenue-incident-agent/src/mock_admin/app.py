"""A small admin service: the six reversible actions an approved mitigation can call.

It exists so that the executor is written against a real network boundary,
with timeouts, retries and the question of acting once, which is where
integrations fail. It needs no secrets and has no authentication; a real admin
API would need service credentials with the narrowest scope that covers the
six actions.

Contract:
- Every action is a POST with an `Idempotency-Key` header and a JSON body that
  carries `incident_id` and `reviewer`.
- An unknown target is 404. A target already in the requested state is 200 with
  `changed: false`.
- The same key with the same request returns the first response and does
  nothing again. The same key with a different request is 409.
- State is a SQLite file, so a restart does not forget what was done.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (target TEXT PRIMARY KEY, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS requests (
    idempotency_key TEXT PRIMARY KEY,
    request TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    response TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applied (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    incident_id TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    idempotency_key TEXT NOT NULL
);
"""


class Body(BaseModel):
    """What every action carries: which incident it is for, and who approved it."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    reviewer: str
    country: str | None = None
    channel: str | None = None


class _Admin:
    def __init__(self, db_path: Path, entities: Mapping[str, Collection[str]]) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.entities = {kind: set(values) for kind, values in entities.items()}
        self._lock = threading.Lock()
        self._con = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._con.executescript(SCHEMA)

    def known(self, kind: str, value: str | None) -> bool:
        return value is not None and value in self.entities.get(kind, set())

    def act(
        self,
        key: str | None,
        path: str,
        body: Body,
        action: str,
        target: dict[str, str | None],
        status: str,
        missing: str | None,
    ) -> JSONResponse:
        """Apply `action` once per idempotency key, and say what happened."""
        if not key:
            return JSONResponse({"detail": "Idempotency-Key header is required"}, 400)
        request = json.dumps({"path": path, "body": body.model_dump()}, sort_keys=True)
        with self._lock:
            seen = self._con.execute(
                "SELECT request, status_code, response FROM requests WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if seen is not None:
                if seen[0] != request:
                    return JSONResponse(
                        {"detail": "this idempotency key was used for a different request"}, 409
                    )
                return JSONResponse(json.loads(seen[2]), seen[1])  # the first answer, again
            answer: dict[str, Any]
            if missing is not None:
                code, answer = 404, {"detail": missing}
            else:
                name = json.dumps(target, sort_keys=True)
                row = self._con.execute(
                    "SELECT status FROM state WHERE target = ?", (f"{action}:{name}",)
                ).fetchone()
                changed = row is None or row[0] != status
                if changed:
                    self._con.execute(
                        "INSERT OR REPLACE INTO state VALUES (?, ?)", (f"{action}:{name}", status)
                    )
                    self._con.execute(
                        "INSERT INTO applied (at, action, target, incident_id, reviewer, "
                        "idempotency_key) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            datetime.now(UTC).isoformat(timespec="seconds"),
                            action,
                            name,
                            body.incident_id,
                            body.reviewer,
                            key,
                        ),
                    )
                code = 200
                answer = {"action": action, "target": target, "state": status, "changed": changed}
            self._con.execute(
                "INSERT INTO requests VALUES (?, ?, ?, ?)", (key, request, code, json.dumps(answer))
            )
        return JSONResponse(answer, code)

    def applied(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._con.execute(
                "SELECT at, action, target, incident_id, reviewer, idempotency_key "
                "FROM applied ORDER BY id"
            ).fetchall()
        names = ("at", "action", "target", "incident_id", "reviewer", "idempotency_key")
        return [
            {**dict(zip(names, row, strict=True)), "target": json.loads(row[2])} for row in rows
        ]


Key = Annotated[str | None, Header(alias="Idempotency-Key")]


def create_app(db_path: Path, entities: Mapping[str, Collection[str]]) -> FastAPI:
    """The service, seeded with what exists: coupons, releases, SKUs, providers, campaigns,
    countries and channels, by kind."""
    admin = _Admin(db_path, entities)
    app = FastAPI(title="Fernhill Home admin (mock)")

    def unknown(kind: str, value: str | None) -> str | None:
        return None if admin.known(kind, value) else f"unknown {kind}: {value}"

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "entities": {k: len(v) for k, v in sorted(admin.entities.items())}}

    @app.get("/actions")
    def actions() -> list[dict[str, Any]]:
        return admin.applied()

    @app.post("/coupons/{code}/disable")
    def disable_coupon(code: str, body: Body, key: Key = None) -> JSONResponse:
        return admin.act(
            key, f"/coupons/{code}/disable", body, "disable_coupon", {"code": code}, "disabled",
            unknown("coupon", code),
        )  # fmt: skip

    @app.post("/releases/{release_id}/rollback")
    def rollback_release(release_id: str, body: Body, key: Key = None) -> JSONResponse:
        return admin.act(
            key, f"/releases/{release_id}/rollback", body, "rollback_release",
            {"release_id": release_id}, "rolled_back", unknown("release", release_id),
        )  # fmt: skip

    @app.post("/listings/{sku}/pause")
    def pause_listing(sku: str, body: Body, key: Key = None) -> JSONResponse:
        return admin.act(
            key, f"/listings/{sku}/pause", body, "pause_listing", {"sku": sku}, "paused",
            unknown("sku", sku),
        )  # fmt: skip

    @app.post("/payment-providers/{provider}/failover")
    def failover(provider: str, body: Body, key: Key = None) -> JSONResponse:
        return admin.act(
            key, f"/payment-providers/{provider}/failover", body, "failover_payment_provider",
            {"provider": provider, "country": body.country}, "failed_over",
            unknown("provider", provider) or unknown("country", body.country),
        )  # fmt: skip

    @app.post("/bot-challenge")
    def bot_challenge(body: Body, key: Key = None) -> JSONResponse:
        return admin.act(
            key, "/bot-challenge", body, "enable_bot_challenge",
            {"country": body.country, "channel": body.channel}, "enabled",
            unknown("country", body.country) or unknown("channel", body.channel),
        )  # fmt: skip

    @app.post("/campaigns/{campaign_id}/resume")
    def resume_campaign(campaign_id: str, body: Body, key: Key = None) -> JSONResponse:
        return admin.act(
            key, f"/campaigns/{campaign_id}/resume", body, "resume_campaign",
            {"campaign_id": campaign_id}, "active", unknown("campaign", campaign_id),
        )  # fmt: skip

    return app
