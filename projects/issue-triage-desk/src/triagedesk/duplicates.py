"""Find earlier issues that may be the same report.

Word search and vector search each return their best earlier issues. The two lists
are fused by rank, so a candidate both found rises to the top. No model is called.
"""

from __future__ import annotations

from .index import IssueIndex
from .models import Candidate, Confidence, Issue

SEARCH_DEPTH = 50
# Reciprocal-rank fusion: score = sum of 1 / (k + rank) over the lists.
DEFAULT_FUSION_K = 10
MODES = ("fused", "vector", "words")


def find_candidates(
    index: IssueIndex,
    issue: Issue,
    limit: int = 20,
    mode: str = "fused",
    fusion_k: int = DEFAULT_FUSION_K,
) -> list[Candidate]:
    """The earlier issues most like this one, best first."""
    if mode not in MODES:
        raise ValueError(f"Unknown retrieval mode: {mode}")
    by_words = index.by_words(issue, SEARCH_DEPTH) if mode != "vector" else []
    by_vector = index.by_vector(issue, SEARCH_DEPTH) if mode != "words" else []
    found: dict[int, Candidate] = {}
    for rank, (number, _) in enumerate(by_words, start=1):
        candidate = found.setdefault(number, Candidate(number=number, score=0.0))
        candidate.lexical_rank = rank
        candidate.score += 1.0 / (fusion_k + rank)
    for rank, (number, similarity) in enumerate(by_vector, start=1):
        candidate = found.setdefault(number, Candidate(number=number, score=0.0))
        candidate.vector_rank = rank
        candidate.similarity = similarity
        candidate.score += 1.0 / (fusion_k + rank)
    ranked = sorted(found.values(), key=lambda c: (-c.score, -c.number))
    return ranked[:limit]


def most_similar(candidates: list[Candidate]) -> Candidate | None:
    """The candidate the vector search found closest, wherever fusion placed it."""
    scored = [c for c in candidates if c.similarity is not None]
    return max(scored, key=lambda c: c.similarity) if scored else None


def similarity_decision(
    candidates: list[Candidate], confident: float, propose: float
) -> tuple[int | None, Confidence | None]:
    """The no-model duplicate call: the most similar earlier issue, if similar enough."""
    best = most_similar(candidates)
    if best is None:
        return None, None
    if best.similarity >= confident:
        return best.number, "high"
    if best.similarity >= propose:
        return best.number, "medium"
    return None, None


class Families:
    """Issues linked as duplicates of one another, as groups.

    Maintainers often close a third report against the second, not the first. A
    proposal that names any issue of the same family found the right report.
    """

    def __init__(self) -> None:
        self._parent: dict[int, int] = {}

    def _find(self, number: int) -> int:
        self._parent.setdefault(number, number)
        root = number
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[number] != root:
            self._parent[number], number = root, self._parent[number]
        return root

    def link(self, duplicate: int, original: int) -> None:
        self._parent[self._find(duplicate)] = self._find(original)

    def same(self, a: int, b: int) -> bool:
        if a == b:
            return True
        if a not in self._parent or b not in self._parent:
            return False
        return self._find(a) == self._find(b)
