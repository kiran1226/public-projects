"""The cascade: tier 1 on every edit, a model on the top slice, one risk for the queue.

Two things live here. `Cascade` turns an edit into a verdict and is called the same
way by the backtest, the replay and the live run. `Admission` is the policy between
the tiers: which edits go on to the model, and what happens when the queue between
the tiers is full or the day's budget is used up. It does no I/O and has no clock of
its own, so the live loop and the load test drive the same policy.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .budget import PacedBudget
from .combine import DEFAULT, Combiner
from .config import Settings
from .features import FEATURES
from .gate import check
from .models import Change, Edit, Tier2Answer, Verdict
from .tier1 import Tier1
from .tier2 import SYSTEM_PROMPT

# Why an edit in the model's slice kept its tier-1 score.
QUEUE_FULL = "queue_full"
BUDGET = "budget"
NO_MODEL = "no_model"
MODEL_ERROR = "model_error"
GATE = "gate"


def system_version(*parts) -> str:
    """A short hash of everything that shapes an answer."""
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]


@dataclass
class Admission:
    """Decides, edit by edit, whether the model reads it. Counts what it turns away."""

    threshold: float
    queue_bound: int
    budget: PacedBudget | None = None
    waiting: int = 0
    counts: dict[str, int] = field(
        default_factory=lambda: {"tier1_only": 0, "admitted": 0, QUEUE_FULL: 0, BUDGET: 0}
    )

    def offer(self, score: float, seconds_into_day: float) -> str | None:
        """None if the edit is admitted to the model's queue, otherwise the reason not.

        An edit below the slice is not a skip: it was never meant for the model.
        """
        if score < self.threshold:
            self.counts["tier1_only"] += 1
            return "below_slice"
        if self.waiting >= self.queue_bound:
            self.counts[QUEUE_FULL] += 1
            return QUEUE_FULL
        if self.budget is not None and not self.budget.allows(seconds_into_day):
            self.counts[BUDGET] += 1
            return BUDGET
        self.waiting += 1
        self.counts["admitted"] += 1
        return None

    def done(self) -> None:
        """An admitted edit left the queue between the tiers."""
        self.waiting = max(0, self.waiting - 1)


@dataclass
class Cascade:
    settings: Settings
    tier1: Tier1
    # The model this cascade asks, or None for tier 1 alone.
    model: str | None = None
    combiner: Combiner = DEFAULT
    share: float | None = None

    @property
    def model_share(self) -> float:
        return self.settings.model_share if self.share is None else self.share

    @property
    def threshold(self) -> float:
        if self.model_share >= 1.0:
            return -1.0  # every edit is in the slice
        return self.tier1.threshold(self.model_share)

    def version(self, config: str) -> str:
        parts: list = [config, list(FEATURES), self.tier1.trained_on, self.tier1.quantiles]
        if self.model is not None:
            c = self.combiner
            parts += [
                self.model, SYSTEM_PROMPT, self.model_share, self.settings.change_chars,
                round(c.intercept, 4), round(c.weight_tier1, 4), round(c.weight_model, 4),
            ]  # fmt: skip
        return system_version(*parts)

    def in_slice(self, score: float) -> bool:
        return score >= self.threshold

    def tier1_verdict(self, score: float, skipped: str | None = None) -> Verdict:
        return Verdict(risk=score, tier1=score, skipped=skipped)

    def model_verdict(
        self, score: float, answer: Tier2Answer | None, change: Change | None
    ) -> Verdict:
        """The verdict for an edit the model was asked about. The gate has the last word."""
        if answer is None or change is None:
            return self.tier1_verdict(score, MODEL_ERROR)
        checked, fired = check(answer, change)
        if checked is None:
            verdict = self.tier1_verdict(score, GATE)
            verdict.gate = fired
            return verdict
        has_range = checked.first_line > 0
        return Verdict(
            risk=self.combiner.risk(score, checked.risk),
            tier1=score,
            model_risk=checked.risk,
            category=checked.category,
            first_line=checked.first_line if has_range else None,
            last_line=checked.last_line if has_range else None,
            source="model",
            gate=fired,
        )


def edit_seconds_into_day(edit: Edit) -> float:
    t = edit.timestamp
    return t.hour * 3600 + t.minute * 60 + t.second
