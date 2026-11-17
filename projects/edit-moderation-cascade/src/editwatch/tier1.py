"""Tier 1: a gradient-boosted classifier on the event's own fields.

It runs on every edit and decides two things: where the edit stands in the queue when
no model has read it, and whether it is in the top slice that a model reads. It is
never used to pass an edit as fine: measured before the build, the safest half of
the queue by this tier still held one reverted edit in six.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

from .features import FEATURES, features
from .models import Edit


@dataclass
class Tier1:
    model: HistGradientBoostingClassifier
    trained_on: int
    reverted_share: float
    # Score quantiles on the training edits: quantiles[q] is the score that the top
    # q of the stream exceeds. Used to decide what is in the model's slice.
    quantiles: dict[float, float]

    def score(self, edit: Edit) -> float:
        """Probability that the edit is reverted, from its event alone."""
        return float(self.model.predict_proba(np.array([features(edit)]))[0, 1])

    def scores(self, edits: list[Edit]) -> np.ndarray:
        if not edits:
            return np.zeros(0)
        return self.model.predict_proba(np.array([features(e) for e in edits]))[:, 1]

    def threshold(self, share: float) -> float:
        """The score an edit must reach to be in the top `share` of the stream."""
        nearest = min(self.quantiles, key=lambda q: abs(q - share))
        if abs(nearest - share) > 0.005:
            known = ", ".join(f"{q:g}" for q in sorted(self.quantiles))
            raise ValueError(f"No slice threshold for a share of {share:g}. Known shares: {known}.")
        return self.quantiles[nearest]

    def summary(self) -> dict:
        return {
            "trained_on": self.trained_on,
            "reverted_share": round(self.reverted_share, 4),
            "features": list(FEATURES),
            "quantiles": {str(q): round(v, 6) for q, v in self.quantiles.items()},
        }


SHARES = (0.02, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50)


def train(edits: list[Edit], reverted: dict[int, bool], seed: int = 17) -> Tier1:
    """Fit on edits with a known outcome. `edits` must all predate the cut."""
    rows = [e for e in edits if e.rev_id in reverted]
    if len(rows) < 50:
        raise ValueError("Tier 1 needs at least 50 edits with an outcome to train on.")
    x = np.array([features(e) for e in rows])
    y = np.array([reverted[e.rev_id] for e in rows], dtype=int)
    if y.min() == y.max():
        raise ValueError("Tier 1 needs both reverted and kept edits to train on.")
    model = HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.06, max_leaf_nodes=31, l2_regularization=1.0,
        early_stopping=True, validation_fraction=0.15, random_state=seed,
    )  # fmt: skip
    model.fit(x, y)
    scores = model.predict_proba(x)[:, 1]
    quantiles = {share: float(np.quantile(scores, 1 - share)) for share in SHARES}
    return Tier1(model, len(rows), float(y.mean()), quantiles)
