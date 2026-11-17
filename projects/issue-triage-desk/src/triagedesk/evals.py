"""Scoring: the desk's answers against what the maintainers did.

Labels are scored as sets, because an issue can carry several areas or none. The
worst error has its own count: a new issue the desk called a duplicate. In apply mode
that is a comment telling a reporter their real, new report already exists.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from .duplicates import Families
from .models import Outcome, Prediction, Taxonomy
from .truth import RESOLVED

HIT_DEPTHS = (1, 3, 5, 8, 20)


@dataclass
class Counts:
    right: int = 0  # proposed and on the issue
    wrong: int = 0  # proposed and not on the issue
    missed: int = 0  # on the issue and not proposed

    @property
    def proposed(self) -> int:
        return self.right + self.wrong

    @property
    def present(self) -> int:
        return self.right + self.missed

    @property
    def precision(self) -> float | None:
        return self.right / self.proposed if self.proposed else None

    @property
    def recall(self) -> float | None:
        return self.right / self.present if self.present else None


@dataclass
class Score:
    config: str
    system_version: str
    issues: int = 0
    # Labels proposed at medium or high confidence, per group.
    labels: dict[str, Counts] = field(default_factory=dict)
    # Labels at high confidence only: what the desk would apply on its own.
    sure: dict[str, Counts] = field(default_factory=dict)
    # Labels proposed on issues the maintainers gave no label of the taxonomy at all
    # (mostly issues closed as duplicates or as not planned). All of them count as
    # wrong above; they are shown apart because nobody knows what the label would be.
    on_unlabelled: int = 0
    sure_on_unlabelled: int = 0
    # Issues the maintainers gave a label of the primary group.
    labelled: int = 0
    # Of those: every proposed primary label is on the issue, and there is at least one.
    primary_right: int = 0
    # Over all issues: the proposed primary set equals the maintainers' set.
    primary_exact: int = 0
    # Duplicates whose original is a known earlier issue of this tracker.
    duplicates: int = 0
    # Duplicates with an original elsewhere or not found. Left out of recall.
    duplicates_unresolved: int = 0
    flagged: int = 0
    flagged_right: int = 0
    flagged_other_issue: int = 0
    flagged_unresolved: int = 0
    # The worst error: called a duplicate, and the maintainers did not close it as one.
    false_duplicates: int = 0
    false_duplicates_sure: int = 0
    hits: dict[int, int] = field(default_factory=lambda: dict.fromkeys(HIT_DEPTHS, 0))
    person: int = 0
    reasons: Counter = field(default_factory=Counter)
    # Issues the desk would handle alone, and those among them with nothing wrong.
    alone: int = 0
    alone_clean: int = 0
    gate: Counter = field(default_factory=Counter)
    model_answers: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    # Live only: predictions written after the tracker already showed the answer.
    seen_first: int = 0

    def all_labels(self, sure: bool = False) -> Counts:
        total = Counts()
        for counts in (self.sure if sure else self.labels).values():
            total.right += counts.right
            total.wrong += counts.wrong
            total.missed += counts.missed
        return total

    @property
    def duplicate_precision(self) -> float | None:
        return self.flagged_right / self.flagged if self.flagged else None

    @property
    def duplicate_recall(self) -> float | None:
        return self.flagged_right / self.duplicates if self.duplicates else None


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """A 95% interval for a share. Sound for small counts, unlike the plain formula."""
    if total == 0:
        return (0.0, 1.0)
    share = successes / total
    denominator = 1 + z * z / total
    centre = (share + z * z / (2 * total)) / denominator
    half = z * math.sqrt(share * (1 - share) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))


def score(
    predictions: list[Prediction],
    outcomes: dict[int, Outcome],
    taxonomy: Taxonomy,
    primary_group: str,
    families: Families | None = None,
    *,
    live: bool = False,
) -> list[Score]:
    """One Score per configuration and system version, over predictions with an outcome.

    With `live`, a prediction counts only if it was written before its outcome was
    read and before the tracker showed a label of the taxonomy or a close.
    """
    families = families or Families()
    groups = sorted({d.group for d in taxonomy.labels})
    scores: dict[tuple[str, str], Score] = {}
    for prediction in predictions:
        outcome = outcomes.get(prediction.number)
        if outcome is None:
            continue
        key = (prediction.config, prediction.system_version)
        s = scores.get(key)
        if s is None:
            s = scores[key] = Score(
                prediction.config,
                prediction.system_version,
                labels={g: Counts() for g in groups},
                sure={g: Counts() for g in groups},
            )
        if live and (
            prediction.seen_labels
            or prediction.seen_closed
            or prediction.written_at >= outcome.read_at
        ):
            s.seen_first += 1
            continue
        _add(s, prediction, outcome, taxonomy, primary_group, families)
    return sorted(scores.values(), key=lambda s: (s.config, s.system_version))


def _add(
    s: Score,
    prediction: Prediction,
    outcome: Outcome,
    taxonomy: Taxonomy,
    primary: str,
    families: Families,
) -> None:
    proposal = prediction.proposal
    s.issues += 1
    s.cost_usd += prediction.cost_usd
    s.latency_ms += prediction.latency_ms
    s.gate.update(proposal.gate)
    if proposal.source == "model":
        s.model_answers += 1
    truth = set(outcome.labels)
    proposed = set(proposal.names("medium"))
    sure = set(proposal.names("high"))
    for group in s.labels:
        in_group = {name for name in taxonomy.names(group)}
        t, p, h = truth & in_group, proposed & in_group, sure & in_group
        s.labels[group].right += len(p & t)
        s.labels[group].wrong += len(p - t)
        s.labels[group].missed += len(t - p)
        s.sure[group].right += len(h & t)
        s.sure[group].wrong += len(h - t)
        s.sure[group].missed += len(t - h)
    if not truth:
        s.on_unlabelled += len(proposed)
        s.sure_on_unlabelled += len(sure)
    primary_names = set(taxonomy.names(primary))
    truth_primary, proposed_primary = truth & primary_names, proposed & primary_names
    if truth_primary:
        s.labelled += 1
        if proposed_primary and proposed_primary <= truth_primary:
            s.primary_right += 1
    if proposed_primary == truth_primary:
        s.primary_exact += 1

    resolved = outcome.duplicate and outcome.original_status == RESOLVED
    duplicate_right = False
    false_duplicate = False
    if resolved:
        s.duplicates += 1
        for depth in HIT_DEPTHS:
            if any(families.same(c, outcome.original) for c in prediction.candidates[:depth]):
                s.hits[depth] += 1
    elif outcome.duplicate:
        s.duplicates_unresolved += 1
    if proposal.duplicate_of is not None:
        s.flagged += 1
        if resolved and families.same(proposal.duplicate_of, outcome.original):
            s.flagged_right += 1
            duplicate_right = True
        elif resolved:
            s.flagged_other_issue += 1
        elif outcome.duplicate:
            s.flagged_unresolved += 1
        else:
            s.false_duplicates += 1
            false_duplicate = True
            if proposal.duplicate_confidence == "high":
                s.false_duplicates_sure += 1

    if proposal.needs_person:
        s.person += 1
        s.reasons.update(proposal.person)
    else:
        s.alone += 1
        labels_fine = sure <= truth
        duplicate_fine = proposal.duplicate_of is None or duplicate_right
        if labels_fine and duplicate_fine and not false_duplicate:
            s.alone_clean += 1


def families_from(outcomes: dict[int, Outcome]) -> Families:
    """Duplicate families from every resolved outcome."""
    families = Families()
    for outcome in outcomes.values():
        if outcome.duplicate and outcome.original is not None:
            families.link(outcome.number, outcome.original)
    return families
