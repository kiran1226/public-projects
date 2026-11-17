"""Scoring: join the lists the system wrote with what suppliers went on to win.

Scoring reads two files and nothing else: the prediction log and the outcome log. It
needs no feed, no database and no model, so anyone can reproduce the numbers.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from .models import Outcome, Shortlist

KS = (1, 3, 5, 15)


@dataclass
class Score:
    """How one ranker did on one set of outcomes."""

    ranker: str
    system_version: str
    mode: str
    outcomes: int = 0
    found: dict[int, int] = field(default_factory=lambda: dict.fromkeys(KS, 0))
    ranks: list[int] = field(default_factory=list)
    # The worst errors: a tender the supplier went on to win that it was never shown.
    removed_by_rule: int = 0
    skipped_by_model: int = 0
    not_listed: int = 0
    written_too_late: int = 0
    lists: int = 0
    listed_entries: int = 0
    candidates: int = 0
    cost_usd: float = 0.0
    assessments: int = 0

    @property
    def median_rank(self) -> float | None:
        return statistics.median(self.ranks) if self.ranks else None

    @property
    def mean_reciprocal_rank(self) -> float:
        return sum(1.0 / r for r in self.ranks) / self.outcomes if self.outcomes else 0.0

    @property
    def mean_list_length(self) -> float:
        return self.listed_entries / self.lists if self.lists else 0.0

    @property
    def mean_candidates(self) -> float:
        return self.candidates / self.lists if self.lists else 0.0

    def share(self, k: int) -> float:
        return self.found[k] / self.outcomes if self.outcomes else 0.0


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """A 95% interval for a share, usable for small counts and shares near 0 or 1."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def score(
    shortlists: Iterable[Shortlist],
    outcomes: Iterable[Outcome],
    *,
    start: date | None = None,
    end: date | None = None,
    only_lists_of: str | None = None,
) -> list[Score]:
    """Score every (mode, ranker, version) in the log on the outcomes in [start, end].

    An outcome counts for a ranker only if that ranker wrote a list for that profile
    on the day the tender was published. `only_lists_of` restricts every ranker to the
    profile-days on which the named ranker also wrote a list, so a ranker that ran on
    a sample (the model) is compared with the others on the same sample.
    """
    in_range = [
        s for s in shortlists if (start is None or s.day >= start) and (end is None or s.day <= end)
    ]
    if only_lists_of:
        allowed = {(s.profile_id, s.day, s.mode) for s in in_range if s.ranker == only_lists_of}
        in_range = [s for s in in_range if (s.profile_id, s.day, s.mode) in allowed]

    scores: dict[tuple[str, str, str], Score] = {}
    index: dict[tuple[str, date], list[Shortlist]] = defaultdict(list)
    for shortlist in in_range:
        group = (shortlist.mode, shortlist.ranker, shortlist.system_version)
        current = scores.setdefault(
            group, Score(ranker=shortlist.ranker, system_version=group[2], mode=group[0])
        )
        current.lists += 1
        current.listed_entries += len(shortlist.entries)
        current.candidates += shortlist.candidates
        current.cost_usd += shortlist.cost_usd
        current.assessments += sum(1 for e in shortlist.entries if e.assessment is not None)
        index[(shortlist.profile_id, shortlist.day)].append(shortlist)

    for outcome in outcomes:
        if outcome.day is None or outcome.publication_number is None:
            continue
        for shortlist in index.get((outcome.profile_id, outcome.day), []):
            current = scores[(shortlist.mode, shortlist.ranker, shortlist.system_version)]
            if shortlist.mode == "live" and shortlist.written_at.date() >= outcome.arrived:
                # Written on or after the day the answer was public: not a prediction.
                current.written_too_late += 1
                continue
            current.outcomes += 1
            entry = next(
                (
                    e
                    for e in shortlist.entries
                    if e.publication_number == outcome.publication_number
                ),
                None,
            )
            if entry is None:
                if outcome.filter_hits:
                    current.removed_by_rule += 1
                else:
                    current.not_listed += 1
                continue
            if entry.assessment is not None and entry.assessment.decision == "skip":
                current.skipped_by_model += 1
            current.ranks.append(entry.rank)
            for k in KS:
                if entry.rank <= k:
                    current.found[k] += 1
    return sorted(scores.values(), key=lambda s: (s.mode, s.ranker, s.system_version))


RANKER_ORDER = ("alert", "cpv", "lexical", "embedding", "buyer", "fused", "fused+model")
RANKER_LABEL = {
    "alert": "Category alert (baseline)",
    "cpv": "Category match, weighted",
    "lexical": "Text match (TF-IDF)",
    "embedding": "Multilingual embeddings",
    "buyer": "Buyer history alone",
    "fused": "Fused (categories and embeddings)",
    "fused+model": "Fused, then the model reads the top of the list",
}


def _ordered(scores: list[Score]) -> list[Score]:
    position = {name: i for i, name in enumerate(RANKER_ORDER)}
    return sorted(scores, key=lambda s: (position.get(s.ranker, 99), s.system_version))


def table(scores: list[Score]) -> str:
    """One row per ranker: of the tenders later won, how many were in the top 1, 3, 5 and 15."""
    lines = [
        "| Ranker | Tenders later won | In top 1 | In top 3 | In top 5 | In top 15 "
        "| Median place | Removed by a rule | Marked skip by the model | Mean list length |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in _ordered(scores):
        if not s.outcomes:
            continue
        median = f"{s.median_rank:g}" if s.median_rank is not None else "n/a"
        skip = str(s.skipped_by_model) if s.assessments else "n/a"
        lines.append(
            f"| {RANKER_LABEL.get(s.ranker, s.ranker)} | {s.outcomes} "
            f"| {s.found[1]} ({s.share(1):.0%}) | {s.found[3]} ({s.share(3):.0%}) "
            f"| {s.found[5]} ({s.share(5):.0%}) | {s.found[15]} ({s.share(15):.0%}) "
            f"| {median} | {s.removed_by_rule} | {skip} | {s.mean_list_length:.1f} |"
        )
    return "\n".join(lines)
