"""Supplier catalog in SQLite with FTS5 retrieval.

Retrieval is deliberately lexical: product codes and sizes ("5x2.5", "630 kVA")
carry almost all the signal, and BM25 over normalised tokens finds them without
an embedding model. The LLM only has to choose among the few candidates returned.
"""

from __future__ import annotations

import csv
import re
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel

STOPWORDS = frozenset(
    "a an and at bitte by der die das den ein eine for für in je mit of or per pc pcs "
    "piece pieces please stk stück the to und von with x zu m mm mm2".split()
)

_SIZE = re.compile(r"(\d+)\s*[x×*g]\s*(\d+(?:\.\d+)?)")
_SIZE_TOKEN = re.compile(r"\d+x(\d+(?:p\d+)?)")
_UNIT_SUFFIX = re.compile(r"(\d)(kva|kv|mm2|mm|kw|a|m)\b")


class Sku(BaseModel):
    sku: str
    category: str
    description: str
    keywords: str
    unit: str
    unit_cost: float
    list_price: float
    moq: float
    lead_time_days: int


def normalize(text: str) -> str:
    """Canonical token form shared by the index and by queries.

    "NYY-J 5x2,5 mm²" and "nyy j 5 X 2.5mm2" both become "nyy j 5x2p5 mm2", so a
    size is one rare token instead of several common digits.
    """
    t = text.lower().replace("²", "2").replace("qmm", "mm2")
    t = re.sub(r"(\d),(\d)", r"\1.\2", t)
    t = _SIZE.sub(r"\1x\2", t)
    t = _UNIT_SUFFIX.sub(r"\1 \2", t)
    t = re.sub(r"(\d)\.(\d)", r"\1p\2", t)
    return " ".join(_stem(tok) for tok in re.sub(r"[^a-z0-9äöüß]+", " ", t).split())


def sizes(text: str) -> set[str]:
    """The sizes a text states, in one spelling.

    "NYY-J 4 X 16" and "4x16 mm²" both give {"4x16"}.
    """
    found = (tok for tok in normalize(text).split() if _SIZE_TOKEN.fullmatch(tok))
    return {tok.replace("p", ".") for tok in found}


def _stem(token: str) -> str:
    """Crude plural stripping so "Mittelklemmen" finds "Mittelklemme" and "rails" finds "rail".

    Applied to the index and to queries alike, so it only has to be consistent.
    """
    if len(token) < 5 or not token.isalpha():
        return token
    if token.endswith("s"):
        token = token[:-1]
    for suffix in ("en", "e", "n"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def _expand_sizes(tokens: list[str]) -> list[str]:
    """Add the second part of a size token: "1x6" also yields "6", "2x630" yields "630"."""
    out: dict[str, None] = {}
    for tok in tokens:
        out.setdefault(tok)
        size = _SIZE_TOKEN.fullmatch(tok)
        if size:
            out.setdefault(size.group(1))
    return list(out)


def query_tokens(text: str) -> list[str]:
    return _expand_sizes([tok for tok in normalize(text).split() if tok not in STOPWORDS])


def index_text(text: str) -> str:
    return " ".join(_expand_sizes(normalize(text).split()))


class Catalog:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.sha256 = ""  # set by load_catalog; identifies the catalog version
        self._lock = threading.Lock()  # one connection, shared by matching threads
        self.conn.row_factory = sqlite3.Row

    def _fetch(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    @classmethod
    def from_csv(cls, path: Path) -> Catalog:
        with path.open(newline="", encoding="utf-8") as fh:
            skus = [Sku(**row) for row in csv.DictReader(fh)]
        # Pricing divides by the list price, so a free product is a setup error.
        unpriced = [s.sku for s in skus if s.list_price <= 0]
        if unpriced:
            raise ValueError(f"{path.name}: list price must be above zero: {', '.join(unpriced)}")
        return cls.from_skus(skus)

    @classmethod
    def from_skus(cls, skus: Iterable[Sku]) -> Catalog:
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.execute(
            "CREATE TABLE skus (sku TEXT PRIMARY KEY, category TEXT, description TEXT, "
            "keywords TEXT, unit TEXT, unit_cost REAL, list_price REAL, moq REAL, "
            "lead_time_days INTEGER)"
        )
        conn.execute("CREATE VIRTUAL TABLE skus_fts USING fts5(sku UNINDEXED, search_text)")
        for item in skus:
            conn.execute(
                "INSERT INTO skus VALUES (?,?,?,?,?,?,?,?,?)", tuple(item.model_dump().values())
            )
            conn.execute(
                "INSERT INTO skus_fts VALUES (?,?)",
                (item.sku, index_text(f"{item.description} {item.keywords}")),
            )
        conn.commit()
        return cls(conn)

    def __len__(self) -> int:
        return self._fetch("SELECT COUNT(*) FROM skus")[0][0]

    def get(self, sku: str) -> Sku | None:
        rows = self._fetch("SELECT * FROM skus WHERE sku = ?", (sku,))
        return Sku(**dict(rows[0])) if rows else None

    def search(self, text: str, limit: int = 8) -> list[tuple[Sku, float]]:
        """Best-first candidates with a relevance score (higher is better)."""
        tokens = query_tokens(text)
        if not tokens:
            return []
        match = " OR ".join(f'"{tok}"' for tok in tokens)
        rows = self._fetch(
            "SELECT s.*, -bm25(skus_fts) AS score FROM skus_fts "
            "JOIN skus s ON s.sku = skus_fts.sku "
            "WHERE skus_fts MATCH ? ORDER BY score DESC, s.sku LIMIT ?",
            (match, limit),
        )
        out = []
        for row in rows:
            data = dict(row)
            score = data.pop("score")
            out.append((Sku(**data), score))
        return out

    def search_text(self, sku: str) -> str:
        rows = self._fetch("SELECT search_text FROM skus_fts WHERE sku = ?", (sku,))
        return rows[0][0] if rows else ""
