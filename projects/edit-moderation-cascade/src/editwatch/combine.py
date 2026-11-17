"""One number from two tiers.

For an edit the model read, the queue needs a single risk. A two-weight logistic
regression turns the tier-1 score and the model's 0 to 100 answer into a probability
of a revert. It is fitted per model on the development days only, and the weights
are committed, so the held-out days and the live run use numbers fixed in advance.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


@dataclass(frozen=True)
class Combiner:
    intercept: float
    weight_tier1: float
    weight_model: float
    fitted_on: int = 0
    reverted: int = 0

    def risk(self, tier1: float, model_risk: int) -> float:
        z = (
            self.intercept
            + self.weight_tier1 * _logit(tier1)
            + self.weight_model * (model_risk / 100)
        )
        return 1 / (1 + math.exp(-z))


# Used where no weights were fitted (the demo): the model's answer, pulled a little
# towards the tier-1 score. Roughly what the fitted weights look like.
DEFAULT = Combiner(intercept=-3.0, weight_tier1=0.4, weight_model=4.0)


def fit(tier1: list[float], model_risk: list[int], reverted: list[bool]) -> Combiner:
    """Fit on edits the model read. Needs both outcomes among them."""
    if len(set(reverted)) < 2 or len(reverted) < 30:
        raise ValueError("Fitting needs at least 30 edits with both outcomes among them.")
    x = np.array([[_logit(t), m / 100] for t, m in zip(tier1, model_risk, strict=True)])
    y = np.array(reverted, dtype=int)
    model = LogisticRegression(C=1.0, max_iter=500).fit(x, y)
    return Combiner(
        intercept=float(model.intercept_[0]),
        weight_tier1=float(model.coef_[0, 0]),
        weight_model=float(model.coef_[0, 1]),
        fitted_on=len(y),
        reverted=int(y.sum()),
    )


def save(combiners: dict[str, Combiner], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {name: asdict(c) for name, c in sorted(combiners.items())}
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


def load(path: Path) -> dict[str, Combiner]:
    if not path.exists():
        return {}
    return {name: Combiner(**fields) for name, fields in json.loads(path.read_text()).items()}
