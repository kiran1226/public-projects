"""The classical tier: TF-IDF and one logistic regression per label.

It is both the baseline the model has to beat and a second opinion beside it. It is
trained on issues opened before the cut date and never sees a later one.

Two thresholds turn a probability into a confidence. Both are chosen on the newest
tenth of the training issues, which the model was not fitted on:

- `propose`: the probability that gives the best F1 over all labels.
- `confident`: the lowest probability at which at least 19 in 20 proposals are right.
  Where no probability gets there, the one with the best precision is used, and the
  validation record says so. On the Godot tracker that is what happened: the
  maintainers' labels are too uneven for a word model to be right 19 times in 20.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

from .models import Issue, LabelPick, Taxonomy
from .text import classifier_text

VALIDATION_SHARE = 0.10
CONFIDENT_PRECISION = 0.95
# A threshold needs this many proposals on the validation slice to be trusted.
MIN_VALIDATION_PROPOSALS = 20
# A label with fewer positives than this in the fitting part gets no model.
MIN_POSITIVES = 5


@dataclass
class Thresholds:
    propose: float = 0.5
    confident: float = 0.9
    # What the validation slice showed at these thresholds.
    validation: dict = field(default_factory=dict)


def _matrix(issues: list[Issue], labels: list[str]) -> np.ndarray:
    column = {label: i for i, label in enumerate(labels)}
    truth = np.zeros((len(issues), len(labels)), dtype=bool)
    for row, issue in enumerate(issues):
        for label in issue.labels:
            if label in column:
                truth[row, column[label]] = True
    return truth


def choose_thresholds(probabilities: np.ndarray, truth: np.ndarray) -> Thresholds:
    """Pick both thresholds from held-out probabilities.

    If no threshold reaches the precision target, `confident` is the one with the
    best precision, and the validation record says the target was not met.
    """
    grid = [round(0.05 * step, 2) for step in range(1, 20)] + [0.97, 0.98, 0.99]
    present = int(truth.sum())
    rows: dict[float, tuple[int, int]] = {}
    for threshold in grid:
        said = probabilities >= threshold
        rows[threshold] = (int(said.sum()), int((said & truth).sum()))

    def precision(threshold: float) -> float:
        proposed, right = rows[threshold]
        return right / proposed if proposed else 0.0

    def f1(threshold: float) -> float:
        recall = rows[threshold][1] / present if present else 0.0
        total = precision(threshold) + recall
        return 2 * precision(threshold) * recall / total if total else 0.0

    # The lowest threshold with the best F1. With nothing to tell them apart: 0.5.
    candidates = [t for t in grid if t <= 0.95]
    best = max(f1(t) for t in candidates)
    propose = next(t for t in candidates if f1(t) == best) if best > 0 else 0.5
    usable = [t for t in grid if t >= propose and rows[t][0] >= MIN_VALIDATION_PROPOSALS]
    reaching = [t for t in usable if precision(t) >= CONFIDENT_PRECISION]
    if reaching:
        confident = reaching[0]
    elif usable:
        confident = max(usable, key=lambda t: (precision(t), t))
    else:
        confident = max(grid[-1], propose)
    return Thresholds(
        propose=propose,
        confident=confident,
        validation={
            "issues": int(truth.shape[0]),
            "labels_present": present,
            "at_propose": {"proposed": rows[propose][0], "right": rows[propose][1]},
            "at_confident": {"proposed": rows[confident][0], "right": rows[confident][1]},
            "confident_target": CONFIDENT_PRECISION,
            "confident_target_met": bool(reaching),
        },
    )


class LabelClassifier:
    def __init__(self, taxonomy: Taxonomy, *, c: float = 10.0, max_features: int = 200_000):
        self.labels = taxonomy.names()
        self.thresholds = Thresholds()
        self.trained_on = 0
        self._c = c
        self._max_features = max_features
        self._vectorizer: TfidfVectorizer | None = None
        self._models: dict[str, LogisticRegression] = {}

    def _fit_part(self, issues: list[Issue]) -> None:
        self._vectorizer = TfidfVectorizer(
            ngram_range=(1, 2),
            sublinear_tf=True,
            min_df=2,
            max_features=self._max_features,
            dtype=np.float32,
        )
        features = self._vectorizer.fit_transform(classifier_text(i.title, i.body) for i in issues)
        truth = _matrix(issues, self.labels)
        self._models = {}
        for column, label in enumerate(self.labels):
            target = truth[:, column]
            if target.sum() < MIN_POSITIVES or target.all():
                continue
            model = LogisticRegression(C=self._c, class_weight="balanced", max_iter=300)
            self._models[label] = model.fit(features, target)

    def fit(self, issues: list[Issue]) -> LabelClassifier:
        """Train on `issues`, oldest first. Thresholds come from the newest tenth."""
        issues = sorted(issues, key=lambda i: (i.created_at, i.number))
        held = max(1, int(len(issues) * VALIDATION_SHARE))
        if len(issues) - held >= 20:
            self._fit_part(issues[:-held])
            validation = issues[-held:]
            probabilities = self._probabilities(validation)
            self.thresholds = choose_thresholds(probabilities, _matrix(validation, self.labels))
        # The final model uses every training issue, including the validation slice.
        self._fit_part(issues)
        self.trained_on = len(issues)
        return self

    def _probabilities(self, issues: list[Issue]) -> np.ndarray:
        features = self._vectorizer.transform(classifier_text(i.title, i.body) for i in issues)
        out = np.zeros((len(issues), len(self.labels)), dtype=np.float32)
        for column, label in enumerate(self.labels):
            model = self._models.get(label)
            if model is not None:
                out[:, column] = model.predict_proba(features)[:, 1]
        return out

    def scores(self, issue: Issue) -> dict[str, float]:
        """Probability per label for one issue."""
        if self._vectorizer is None:
            return {}
        row = self._probabilities([issue])[0]
        return {label: float(p) for label, p in zip(self.labels, row, strict=True)}

    def picks(self, scores: dict[str, float]) -> list[LabelPick]:
        """Labels at or above the proposing threshold, most probable first."""
        picks = []
        for label, probability in sorted(scores.items(), key=lambda kv: -kv[1]):
            if probability >= self.thresholds.confident:
                picks.append(LabelPick(name=label, confidence="high"))
            elif probability >= self.thresholds.propose:
                picks.append(LabelPick(name=label, confidence="medium"))
        return picks

    def top_terms(self, label: str, issue: Issue, limit: int = 5) -> list[str]:
        """The words of this issue that pushed the label's score up the most."""
        model = self._models.get(label)
        if model is None or self._vectorizer is None:
            return []
        row = self._vectorizer.transform([classifier_text(issue.title, issue.body)])
        weights = row.multiply(model.coef_[0]).tocoo()
        names = self._vectorizer.get_feature_names_out()
        ranked = sorted(zip(weights.data, weights.col, strict=True), reverse=True)
        return [str(names[col]) for weight, col in ranked[:limit] if weight > 0]
