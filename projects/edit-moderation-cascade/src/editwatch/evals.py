"""Scoring: how many of the edits that were later reverted are near the top of the queue.

A reviewer works down the queue and stops when the day is over. So the number that
matters is the share of reverted edits inside the part of the queue they reach: the
top 2%, 5% or 10%. The worst error has its own line: reverted edits in the bottom
half of the queue, which no reviewer working from the top would get to.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from .models import Prediction

SHARES = (0.02, 0.05, 0.10, 0.20)


@dataclass
class Score:
    config: str
    system_version: str
    edits: int = 0
    reverted: int = 0
    # Reverted edits inside the top share of the queue, by share.
    hits: dict[float, int] = field(default_factory=dict)
    # How many edits each share of the queue is.
    depth: dict[float, int] = field(default_factory=dict)
    # The worst error: reverted edits in the bottom half of the queue.
    bottom_half: int = 0
    # Edits the model read, and reverted edits it did not read.
    model_read: int = 0
    reverted_not_read: int = 0
    skipped: Counter = field(default_factory=Counter)
    gate: Counter = field(default_factory=Counter)
    categories: Counter = field(default_factory=Counter)
    cost_usd: float = 0.0
    latencies: list[float] = field(default_factory=list)

    def share_found(self, share: float) -> float | None:
        return self.hits[share] / self.reverted if self.reverted else None

    @property
    def cost_per_1000(self) -> float:
        return 1000 * self.cost_usd / self.edits if self.edits else 0.0


def rank(rows: list[tuple[int, float]]) -> list[int]:
    """Revision ids, riskiest first. Ties go to the earlier revision, so the order is fixed."""
    return [rev for rev, _ in sorted(rows, key=lambda r: (-r[1], r[0]))]


def score_queue(
    config: str,
    version: str,
    risks: dict[int, float],
    reverted: dict[int, bool],
    shares: tuple[float, ...] = SHARES,
) -> Score:
    """Score one queue: `risks` by revision id, against which revisions were reverted."""
    rows = [(rev, risk) for rev, risk in risks.items() if rev in reverted]
    order = rank(rows)
    s = Score(config, version, edits=len(order), reverted=sum(reverted[r] for r in order))
    for share in shares:
        depth = math.ceil(len(order) * share)
        s.depth[share] = depth
        s.hits[share] = sum(reverted[r] for r in order[:depth])
    s.bottom_half = sum(reverted[r] for r in order[len(order) // 2 :])
    return s


def score(
    predictions: list[Prediction],
    reverted: dict[int, bool],
    only: set[int] | None = None,
) -> list[Score]:
    """One Score per configuration and system version, over edits with an outcome."""
    groups: dict[tuple[str, str], list[Prediction]] = {}
    for p in predictions:
        if p.rev_id not in reverted or (only is not None and p.rev_id not in only):
            continue
        groups.setdefault((p.config, p.system_version), []).append(p)
    scores = []
    for (config, version), rows in sorted(groups.items()):
        s = score_queue(config, version, {p.rev_id: p.verdict.risk for p in rows}, reverted)
        for p in rows:
            v = p.verdict
            s.cost_usd += p.cost_usd
            s.gate.update(v.gate)
            if v.skipped:
                s.skipped[v.skipped] += 1
            if v.source == "model":
                s.model_read += 1
                s.categories[v.category or "none"] += 1
            elif reverted[p.rev_id]:
                s.reverted_not_read += 1
            if p.latency_s is not None:
                s.latencies.append(p.latency_s)
        scores.append(s)
    return scores


def at_share(
    rows: list[Prediction], threshold: float, reverted: dict[int, bool], config: str
) -> Score:
    """The same cascade with a smaller slice: the model's answer counts only above `threshold`.

    A model answer on an edit below the threshold is set aside and the edit keeps its
    tier-1 score, as it would have if the model had never been asked.
    """
    risks = {}
    read = 0
    cost = 0.0
    for p in rows:
        v = p.verdict
        if v.source == "model" and v.tier1 >= threshold:
            risks[p.rev_id] = v.risk
            read += 1
            cost += p.cost_usd
        else:
            risks[p.rev_id] = v.tier1
    s = score_queue(config, rows[0].system_version if rows else "", risks, reverted)
    s.model_read = read
    s.cost_usd = cost
    return s


def arrival_order(edits: int, reverted: int, shares: tuple[float, ...] = SHARES) -> Score:
    """What reviewers have today: the queue in the order edits arrive.

    Risk plays no part in that order, so the top k% of the queue holds k% of the
    reverted edits on average. The counts are that expectation, rounded.
    """
    s = Score("arrival", "", edits=edits, reverted=reverted)
    for share in shares:
        s.depth[share] = math.ceil(edits * share)
        s.hits[share] = math.floor(reverted * share + 0.5)
    s.bottom_half = math.floor(reverted / 2 + 0.5)
    return s


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """A 95% interval for a share. Sound for small counts."""
    if total == 0:
        return (0.0, 1.0)
    share = successes / total
    denominator = 1 + z * z / total
    centre = (share + z * z / (2 * total)) / denominator
    half = z * math.sqrt(share * (1 - share) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))
