"""Text embeddings from a small English model that runs on a CPU.

Two reports of the same problem often share few words: "editor freezes when I rename a
node" and "renaming nodes hangs the IDE". The embedding model maps both to nearby
vectors, which word search cannot do.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Protocol

import numpy as np

# 384 dimensions, about 70 MB, MIT licence. Downloaded on first use.
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
# The title and the start of the description say what is wrong. Measured on this
# project's issues on a laptop CPU: 12 issues a second at 1,200 characters, 33 at 600.
MAX_CHARS = 600


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

    def __init__(self, model: str = DEFAULT_MODEL, batch_size: int = 16):
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
