"""Rankers: given a profile and the day's tenders, which should be read first.

Six of them, from what a bid desk has today to the best free combination:

- alert      the category alert: every tender sharing a category with the profile,
             in publication order. This is the baseline.
- cpv        category match, weighted by how often the supplier won in a category
             and how deep the match goes.
- lexical    word and character overlap (TF-IDF) with the text of past wins.
- embedding  a multilingual embedding of the same texts, which works across languages.
- buyer      whether the supplier has won from this buyer before.
- fused      categories and embeddings combined by reciprocal rank. Which parts go
             in was decided on the development months; see the decision log.

None of them calls a paid model.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from . import cpv
from .awards import buyer_key
from .embed import Embedder
from .models import Notice, Profile, Scored

# What a category match is worth by the number of leading digits shared.
DEPTH_VALUE = {2: 0.10, 3: 0.25, 4: 0.45, 5: 0.70, 6: 0.85, 7: 0.95, 8: 1.00}
EXCLUDED = float("-inf")


class Ranker(Protocol):
    name: str

    def scores(self, profile: Profile, notices: Sequence[Notice]) -> np.ndarray:
        """One score per notice, higher first. EXCLUDED keeps a notice off the list."""
        ...


def rank(ranker: Ranker, profile: Profile, notices: Sequence[Notice]) -> list[Scored]:
    """Notices in list order. Ties are broken by publication number, so reruns agree."""
    if not notices:
        return []
    scores = ranker.scores(profile, notices)
    order = sorted(
        (i for i in range(len(notices)) if scores[i] != EXCLUDED),
        key=lambda i: (-float(scores[i]), notices[i].publication_number),
    )
    return [
        Scored(
            procedure_id=notices[i].procedure_id,
            publication_number=notices[i].publication_number,
            score=round(float(scores[i]), 6),
        )
        for i in order
    ]


class AlertRanker:
    """The category alert a bid desk has today.

    A tender is on the list if one of its category codes shares `depth` leading digits
    with a category the supplier has won in. The list is in publication order: an
    alert does not rank.
    """

    def __init__(self, depth: int = 5):
        self.depth = depth
        self.name = "alert"

    def scores(self, profile: Profile, notices: Sequence[Notice]) -> np.ndarray:
        wanted = {cpv.prefix(code, self.depth) for code in profile.cpv_weights}
        wanted.discard("")
        ordered = sorted(range(len(notices)), key=lambda i: notices[i].publication_number)
        scores = np.full(len(notices), EXCLUDED)
        for position, i in enumerate(ordered):
            if any(cpv.prefix(code, self.depth) in wanted for code in notices[i].cpv):
                scores[i] = 1.0 - position / (len(notices) + 1)
        return scores


class CpvRanker:
    """Category match weighted by the supplier's history and by the depth of the match."""

    name = "cpv"

    def scores(self, profile: Profile, notices: Sequence[Notice]) -> np.ndarray:
        total = sum(profile.cpv_weights.values()) or 1.0
        scores = np.zeros(len(notices))
        cache: dict[str, float] = {}
        for i, notice in enumerate(notices):
            best_per_code = []
            for code in notice.cpv:
                if code not in cache:
                    cache[code] = max(
                        (
                            (weight / total) ** 0.5
                            * DEPTH_VALUE.get(cpv.shared_depth(code, own), 0.0)
                            for own, weight in profile.cpv_weights.items()
                        ),
                        default=0.0,
                    )
                best_per_code.append(cache[code])
            if best_per_code:
                scores[i] = max(best_per_code) + 0.1 * float(np.mean(best_per_code))
        return scores


class LexicalRanker:
    """TF-IDF over character n-grams: finds shared words and word stems in one language."""

    name = "lexical"

    def __init__(self, corpus: Sequence[str], top: int = 3):
        # Character n-grams inside word boundaries cope with inflection in Polish,
        # Czech and German better than whole words do.
        self._vectorizer = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(3, 5),
            lowercase=True,
            sublinear_tf=True,
            min_df=2,
            max_features=300_000,
            dtype=np.float32,
        )
        self._vectorizer.fit(corpus if len(corpus) else ["placeholder text"])
        self._top = top
        self._profile_cache: dict[str, object] = {}
        self._notice_cache: dict[str, object] = {}

    def _profile_matrix(self, profile: Profile):
        if profile.profile_id not in self._profile_cache:
            texts = profile.texts or [profile.description or ""]
            self._profile_cache[profile.profile_id] = self._vectorizer.transform(texts)
        return self._profile_cache[profile.profile_id]

    def _notice_matrix(self, notices: Sequence[Notice]):
        from scipy.sparse import vstack

        missing = [n for n in notices if n.publication_number not in self._notice_cache]
        if missing:
            matrix = self._vectorizer.transform([n.text[:4000] for n in missing])
            for row, notice in enumerate(missing):
                self._notice_cache[notice.publication_number] = matrix[row]
        return vstack([self._notice_cache[n.publication_number] for n in notices])

    def scores(self, profile: Profile, notices: Sequence[Notice]) -> np.ndarray:
        similarity = (self._notice_matrix(notices) @ self._profile_matrix(profile).T).toarray()
        return _mean_of_top(similarity, self._top)


class EmbeddingRanker:
    """Multilingual embeddings: a tender is close to the wins it most resembles."""

    name = "embedding"

    def __init__(self, embedder: Embedder, top: int = 3):
        self._embedder = embedder
        self._top = top
        self._profile_cache: dict[str, np.ndarray] = {}
        self._notice_cache: dict[str, np.ndarray] = {}

    def _profile_matrix(self, profile: Profile) -> np.ndarray:
        if profile.profile_id not in self._profile_cache:
            texts = profile.texts or [profile.description or ""]
            self._profile_cache[profile.profile_id] = self._embedder.embed(texts)
        return self._profile_cache[profile.profile_id]

    def warm(self, notices: Sequence[Notice]) -> None:
        """Embed many notices in one batch. Optional; `scores` embeds what is missing."""
        missing = [n for n in notices if n.publication_number not in self._notice_cache]
        if missing:
            vectors = self._embedder.embed([n.text for n in missing])
            for notice, vector in zip(missing, vectors, strict=True):
                self._notice_cache[notice.publication_number] = vector

    def scores(self, profile: Profile, notices: Sequence[Notice]) -> np.ndarray:
        self.warm(notices)
        matrix = np.vstack([self._notice_cache[n.publication_number] for n in notices])
        return _mean_of_top(matrix @ self._profile_matrix(profile).T, self._top)


class BuyerRanker:
    """Has this supplier won from this buyer before.

    Public buyers come back to suppliers they know, and a supplier knows which of its
    customers is tendering again. A buyer the supplier has never won from scores zero,
    which leaves the order to the other rankers.
    """

    name = "buyer"

    def scores(self, profile: Profile, notices: Sequence[Notice]) -> np.ndarray:
        scores = np.zeros(len(notices))
        if not profile.buyers:
            return scores
        for i, notice in enumerate(notices):
            key = buyer_key(notice.buyer_name, notice.buyer_country)
            if key and key in profile.buyers:
                scores[i] = float(np.log1p(profile.buyers[key]))
        return scores


def _mean_of_top(similarity: np.ndarray, top: int) -> np.ndarray:
    """For each notice, the mean of its `top` best similarities to the profile's texts.

    A supplier with two lines of business has wins that look nothing like each other.
    Comparing a tender with the average of all wins would blur both lines; comparing it
    with the few wins it most resembles does not.
    """
    if similarity.shape[1] <= top:
        return similarity.mean(axis=1)
    partitioned = np.partition(similarity, -top, axis=1)[:, -top:]
    return partitioned.mean(axis=1)


class FusedRanker:
    """Reciprocal rank fusion: a tender several rankers place high comes first."""

    def __init__(self, rankers: Sequence[Ranker], k: int = 20, name: str = "fused"):
        self._rankers = list(rankers)
        self._k = k
        self.name = name

    def scores(self, profile: Profile, notices: Sequence[Notice]) -> np.ndarray:
        fused = np.zeros(len(notices))
        tie_break = np.argsort(np.argsort([n.publication_number for n in notices]))
        for ranker in self._rankers:
            scores = ranker.scores(profile, notices)
            # Rank positions, best first; equal scores are ordered by publication number.
            order = np.lexsort((tie_break, -scores))
            positions = np.empty(len(notices), dtype=float)
            positions[order] = np.arange(1, len(notices) + 1)
            contribution = 1.0 / (self._k + positions)
            contribution[scores == EXCLUDED] = 0.0
            # A ranker with nothing to say about a notice (score 0) adds nothing.
            contribution[scores == 0] = 0.0
            fused += contribution
        return fused
