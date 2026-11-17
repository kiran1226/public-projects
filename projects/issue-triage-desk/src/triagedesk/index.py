"""Search over earlier issues: words (SQLite FTS5) and vectors (a local embedding model).

Every search takes the new issue's creation time and returns only issues opened
before it. That one rule is what makes a replay honest: an issue from March cannot
be matched to one from April, in the backtest or live.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from .embed import Embedder
from .models import Issue
from .store import Store
from .text import problem_text, search_text, words

# How many distinct words of the description join the title's words in a word query.
QUERY_BODY_WORDS = 24
EMBED_BATCH = 256


def word_query(title: str, body: str) -> str:
    """An FTS5 query: any of the title's words or the first words of the description."""
    terms = list(dict.fromkeys(words(title)))
    for term in words(problem_text(body)):
        if len(terms) >= len(set(words(title))) + QUERY_BODY_WORDS:
            break
        if term not in terms:
            terms.append(term)
    # Quoting keeps FTS5 from reading a word such as "and" or "near" as an operator.
    return " OR ".join('"' + term.replace('"', "") + '"' for term in terms)


class IssueIndex:
    def __init__(self, store: Store, embedder: Embedder):
        self.store = store
        self.embedder = embedder
        self._numbers = np.zeros(0, dtype=np.int64)
        self._times = np.zeros(0)
        self._matrix = np.zeros((0, 0), dtype=np.float32)
        self._row: dict[int, int] = {}

    def __len__(self) -> int:
        return len(self._numbers)

    def embed_missing(self, progress: Callable[[str], None] = lambda line: None) -> int:
        """Embed every stored issue that has no vector yet. Returns how many."""
        missing = self.store.missing_vectors(self.embedder.name)
        for start in range(0, len(missing), EMBED_BATCH):
            numbers = missing[start : start + EMBED_BATCH]
            issues = [self.store.issue(number) for number in numbers]
            vectors = self.embedder.embed([search_text(i.title, i.body) for i in issues])
            self.store.add_vectors(self.embedder.name, numbers, vectors)
            if (start // EMBED_BATCH) % 10 == 0:
                progress(f"embedded {min(start + EMBED_BATCH, len(missing))} of {len(missing)}")
        return len(missing)

    def load(self) -> IssueIndex:
        """Read the vectors into memory, oldest issue first."""
        self._numbers, self._times, self._matrix = self.store.vectors(self.embedder.name)
        self._row = {int(number): row for row, number in enumerate(self._numbers)}
        return self

    def refresh(self) -> int:
        """Embed new issues and reload. Called after the store gained issues."""
        added = self.embed_missing()
        self.load()
        return added

    def by_words(self, issue: Issue, limit: int) -> list[tuple[int, float]]:
        found = self.store.search_words(
            word_query(issue.title, issue.body), issue.created_at, limit + 1
        )
        return [(number, score) for number, score in found if number != issue.number][:limit]

    def by_vector(self, issue: Issue, limit: int) -> list[tuple[int, float]]:
        """Earlier issues by cosine similarity, best first."""
        if not len(self._numbers):
            return []
        row = self._row.get(issue.number)
        if row is not None:
            query = self._matrix[row]
        else:
            query = self.embedder.embed([search_text(issue.title, issue.body)])[0]
        end = int(np.searchsorted(self._times, issue.created_at.timestamp(), side="left"))
        if end == 0:
            return []
        similarities = self._matrix[:end] @ query
        take = min(limit + 1, end)
        top = np.argpartition(-similarities, take - 1)[:take]
        top = top[np.argsort(-similarities[top])]
        found = [(int(self._numbers[i]), float(similarities[i])) for i in top]
        return [(number, score) for number, score in found if number != issue.number][:limit]
