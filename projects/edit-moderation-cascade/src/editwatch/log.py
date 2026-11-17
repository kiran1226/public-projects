"""The prediction log: append-only, one row per edit, configuration and system version.

A row is written when the cascade answers and is never rewritten. The log holds
revision ids, scores, a category and line numbers. It holds no page text, no edit
summary and no account name.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterable
from pathlib import Path

from .models import Prediction, Verdict


def to_row(p: Prediction) -> dict:
    v = p.verdict
    row = {
        "r": p.rev_id,
        "t": p.made_at.isoformat(timespec="seconds"),
        "w": p.written_at.isoformat(timespec="seconds"),
        "v": p.system_version,
        "cfg": p.config,
        "risk": round(v.risk, 5),
        "t1": round(v.tier1, 5),
    }
    if v.source == "model":
        row["m"] = v.model_risk
        row["cat"] = v.category
        if v.first_line is not None:
            row["ln"] = [v.first_line, v.last_line]
    if v.skipped:
        row["skip"] = v.skipped
    if v.gate:
        row["g"] = v.gate
    if p.cost_usd:
        row["usd"] = round(p.cost_usd, 6)
    if p.latency_s is not None:
        row["s"] = round(p.latency_s, 3)
    return row


def from_row(row: dict) -> Prediction:
    lines = row.get("ln")
    return Prediction(
        rev_id=row["r"],
        made_at=row["t"],
        written_at=row["w"],
        system_version=row["v"],
        config=row["cfg"],
        verdict=Verdict(
            risk=row["risk"],
            tier1=row["t1"],
            model_risk=row.get("m"),
            category=row.get("cat"),
            first_line=lines[0] if lines else None,
            last_line=lines[1] if lines else None,
            source="model" if "m" in row else "tier1",
            skipped=row.get("skip"),
            gate=row.get("g", []),
        ),
        cost_usd=row.get("usd", 0.0),
        latency_s=row.get("s"),
    )


def _open(path: Path, mode: str):
    if path.suffix == ".gz":
        return gzip.open(path, mode + "t", encoding="utf-8")
    return open(path, mode, encoding="utf-8")


class PredictionLog:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._keys: set[tuple[int, str, str]] | None = None

    def read(self) -> list[Prediction]:
        if not self.path.exists():
            return []
        with _open(self.path, "r") as handle:
            return [from_row(json.loads(line)) for line in handle if line.strip()]

    def keys(self) -> set[tuple[int, str, str]]:
        if self._keys is None:
            self._keys = {p.key for p in self.read()}
        return self._keys

    def has(self, rev_id: int, config: str, version: str) -> bool:
        return (rev_id, config, version) in self.keys()

    def append(self, predictions: Iterable[Prediction]) -> int:
        """Add rows that are not in the log yet. Returns how many were added."""
        keys = self.keys()
        fresh = []
        for prediction in predictions:
            if prediction.key not in keys:
                keys.add(prediction.key)
                fresh.append(prediction)
        if fresh:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with _open(self.path, "a") as handle:
                for prediction in fresh:
                    handle.write(json.dumps(to_row(prediction)) + "\n")
        return len(fresh)
