"""Text embeddings from a small multilingual model that runs on a CPU.

Tenders are written in the buyer's language. A Polish notice and a German one about
the same analyser share almost no words, so word matching alone cannot connect them.
The embedding model maps both to nearby vectors.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import numpy as np

# 384 dimensions, about 220 MB, Apache-2.0. Downloaded on first use.
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
# The model reads a limited number of tokens; the start of a notice says what is bought.
MAX_CHARS = 700


def _normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class Embedder(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """One unit-length row per text."""
        ...


class FastEmbedder:
    """The real model, through fastembed (ONNX runtime, no GPU needed)."""

    def __init__(self, model: str = DEFAULT_MODEL, batch_size: int = 64):
        self.name = model
        self._batch_size = batch_size
        self._model = None

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(self.name)
        vectors = list(
            self._model.embed([t[:MAX_CHARS] for t in texts], batch_size=self._batch_size)
        )
        return _normalise(np.asarray(vectors, dtype=np.float32))


class HashEmbedder:
    """A stand-in for tests: words hashed into a small vector. No download, deterministic."""

    def __init__(self, dim: int = 64):
        self.name = f"hash-{dim}"
        self._dim = dim

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), self._dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in re.findall(r"\w+", text.lower()):
                digest = hashlib.sha256(word.encode()).digest()
                matrix[row, int.from_bytes(digest[:4], "big") % self._dim] += 1.0
        return _normalise(matrix)


class CachedEmbedder:
    """Remembers vectors on disk, keyed by model and text, so a text is embedded once."""

    def __init__(self, inner: Embedder, path: Path | str):
        self.inner = inner
        self.name = inner.name
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Wait rather than fail if another run is writing vectors at the same moment.
        self._db = sqlite3.connect(str(path), timeout=60)
        self._db.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, vector BLOB)")
        self.misses = 0

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.name}\n{text[:MAX_CHARS]}".encode()).hexdigest()

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        keys = [self._key(text) for text in texts]
        found: dict[str, np.ndarray] = {}
        unique = list(dict.fromkeys(keys))
        for start in range(0, len(unique), 500):
            chunk = unique[start : start + 500]
            marks = ",".join("?" * len(chunk))
            for key, blob in self._db.execute(
                f"SELECT key, vector FROM vectors WHERE key IN ({marks})", chunk
            ):
                found[key] = np.frombuffer(blob, dtype=np.float32)
        missing: dict[str, str] = {}
        for key, text in zip(keys, texts, strict=True):
            if key not in found:
                missing.setdefault(key, text)
        if missing:
            self.misses += len(missing)
            vectors = self.inner.embed(list(missing.values()))
            rows = []
            for key, vector in zip(missing, vectors, strict=True):
                found[key] = vector
                rows.append((key, vector.astype(np.float32).tobytes()))
            self._db.executemany("INSERT OR REPLACE INTO vectors (key, vector) VALUES (?, ?)", rows)
            self._db.commit()
        if not keys:
            return np.zeros((0, 0), dtype=np.float32)
        return np.vstack([found[key] for key in keys])
