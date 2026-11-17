"""The prediction log: append-only, one row per issue, configuration and system version.

A row is written when the desk answers and is never rewritten. Running the same
issue again under the same version adds nothing, so a rerun cannot improve a score.
The log holds issue numbers and the desk's answers. It holds no issue text.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path

from .models import LabelPick, Prediction, Proposal

_SHORT = {"high": "h", "medium": "m", "low": "l"}
_LONG = {short: long for long, short in _SHORT.items()}


def system_version(*parts) -> str:
    """A short hash of everything that shapes an answer.

    Configuration, taxonomy, thresholds, prompts and the model name go in. Change any
    of them and the desk is a different system with its own line on the scoreboard.
    """
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]


def to_row(prediction: Prediction) -> dict:
    proposal = prediction.proposal
    row = {
        "n": prediction.number,
        "c": prediction.created_at.isoformat(timespec="seconds"),
        "w": prediction.written_at.isoformat(timespec="seconds"),
        "v": prediction.system_version,
        "cfg": prediction.config,
        "src": proposal.source,
        "l": [[pick.name, _SHORT[pick.confidence]] for pick in proposal.labels],
        "cand": prediction.candidates,
    }
    if proposal.duplicate_of is not None:
        row["dup"] = [proposal.duplicate_of, _SHORT[proposal.duplicate_confidence or "low"]]
    if proposal.person:
        row["p"] = proposal.person
    if proposal.gate:
        row["g"] = proposal.gate
    if prediction.cost_usd:
        row["usd"] = round(prediction.cost_usd, 6)
    if prediction.latency_ms:
        row["ms"] = prediction.latency_ms
    if prediction.seen_labels:
        row["sl"] = 1
    if prediction.seen_closed:
        row["sc"] = 1
    return row


def from_row(row: dict) -> Prediction:
    duplicate = row.get("dup")
    return Prediction(
        number=row["n"],
        created_at=row["c"],
        written_at=row["w"],
        system_version=row["v"],
        config=row["cfg"],
        proposal=Proposal(
            labels=[LabelPick(name=name, confidence=_LONG[c]) for name, c in row["l"]],
            duplicate_of=duplicate[0] if duplicate else None,
            duplicate_confidence=_LONG[duplicate[1]] if duplicate else None,
            person=row.get("p", []),
            gate=row.get("g", []),
            source=row["src"],
        ),
        candidates=row.get("cand", []),
        cost_usd=row.get("usd", 0.0),
        latency_ms=row.get("ms", 0),
        seen_labels=bool(row.get("sl")),
        seen_closed=bool(row.get("sc")),
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

    def has(self, number: int, config: str, version: str) -> bool:
        return (number, config, version) in self.keys()

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
                    handle.write(json.dumps(to_row(prediction), ensure_ascii=False) + "\n")
        return len(fresh)
