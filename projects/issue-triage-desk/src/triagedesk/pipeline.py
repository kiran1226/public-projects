"""One issue through the desk. The backtest and the live run both call `process_issue`.

There is no second code path for evaluation: a replay feeds stored issues through
this function in the order they were opened, and the hourly run feeds it the issues
that have just arrived.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

from .classifier import LabelClassifier
from .config import Settings
from .duplicates import find_candidates, most_similar, similarity_decision
from .gate import gate
from .index import IssueIndex
from .log import system_version
from .models import Candidate, Issue, LabelPick, Prediction, Proposal, Taxonomy
from .rules import Rules
from .store import Store
from .triage import Triager

CONFIGS = ("rules", "classifier", "model-alone", "model", "cascade")
# Configurations that can call the paid model, and the prompt variant each one uses.
PROMPT_OF = {"model-alone": "model-alone", "model": "model", "cascade": "model"}
CONFIG_LABEL = {
    "rules": "Rules (keywords)",
    "classifier": "Classifier (TF-IDF, logistic regression)",
    "model-alone": "Model alone (label names only, no candidates)",
    "model": "Model with definitions and candidates",
    "cascade": "Cascade (classifier when sure, model otherwise)",
}


@dataclass
class Desk:
    """Everything needed to answer one issue."""

    settings: Settings
    taxonomy: Taxonomy
    store: Store
    index: IssueIndex
    rules: Rules
    classifier: LabelClassifier
    # Keyed by prompt variant: "model" and "model-alone".
    triagers: dict[str, Triager] = field(default_factory=dict)

    @property
    def primary_group(self) -> str:
        return self.settings.groups[0].name

    def version(self, config: str) -> str:
        s = self.settings
        parts: list = [
            config,
            s.repo,
            [(g.name, g.prefix, g.min_issues, g.hint) for g in s.groups],
            s.candidates,
            s.retrieval,
            s.fusion_k,
            self.taxonomy.model_dump(mode="json"),
        ]
        if config == "rules":
            parts.append(sorted(s.keywords.items()))
        if config in ("classifier", "cascade") or config in PROMPT_OF:
            t = self.classifier.thresholds
            parts += [self.classifier.trained_on, t.propose, t.confident]
            parts += [s.duplicate_confident, s.duplicate_propose]
        if config in PROMPT_OF:
            triager = self.triagers.get(PROMPT_OF[config])
            if triager is not None:
                provider = triager.provider
                parts += [
                    provider.model, getattr(provider, "params", {}), triager.system,
                    s.model_candidates, "second-opinion-1",
                ]  # fmt: skip
        return system_version(*parts)


def second_opinion(
    picks: list[LabelPick], scores: dict[str, float], propose: float
) -> list[LabelPick]:
    """Lower the confidence of a model label the classifier does not propose.

    Measured on the development months: a label the model is sure of is right far
    more often when the classifier proposes it too. So the model's "high" stands
    only with the classifier's agreement, and its "medium" without that agreement
    is not proposed. The classifier can lower a confidence; it cannot raise one or
    add a label.
    """
    step_down = {"high": "medium", "medium": "low", "low": "low"}
    adjusted = []
    for pick in picks:
        agrees = scores.get(pick.name, 0.0) >= propose
        confidence = pick.confidence if agrees else step_down[pick.confidence]
        adjusted.append(LabelPick(name=pick.name, confidence=confidence))
    return adjusted


def classifier_is_sure(picks: list[LabelPick], candidates: list[Candidate], desk: Desk) -> bool:
    """True when the cascade lets the classifier answer without the model.

    It needs a confident label of the primary group, no label it is unsure about,
    and no earlier issue similar enough to raise the duplicate question.
    """
    if not picks or any(p.confidence != "high" for p in picks):
        return False
    if not any(desk.taxonomy.group_of(p.name) == desk.primary_group for p in picks):
        return False
    closest = most_similar(candidates)
    return closest is None or closest.similarity < desk.settings.duplicate_propose


def process_issue(
    desk: Desk,
    issue: Issue,
    config: str,
    *,
    now: datetime,
    seen_labels: bool = False,
    seen_closed: bool = False,
) -> tuple[Prediction, str | None]:
    """Answer one issue under one configuration. Returns the log row and the model's note."""
    if config not in CONFIGS:
        raise ValueError(f"Unknown configuration: {config}")
    settings = desk.settings
    started = time.perf_counter()
    candidates = find_candidates(
        desk.index, issue, settings.candidates, settings.retrieval, settings.fusion_k
    )

    def is_older(number: int) -> bool:
        other = desk.store.issue(number)
        return other is not None and other.created_at < issue.created_at

    def gated(labels, duplicate, confidence, allowed, source, unavailable=False) -> Proposal:
        return gate(
            labels, duplicate, confidence,
            taxonomy=desk.taxonomy, issue_number=issue.number, allowed_duplicates=allowed,
            is_older=is_older, primary_group=desk.primary_group, source=source,
            model_unavailable=unavailable,
        )  # fmt: skip

    all_numbers = {c.number for c in candidates}
    note = None
    cost = 0.0
    model_seconds = 0.0

    if config == "rules":
        proposal = gated(
            desk.rules.labels(issue.title, issue.body), None, None, all_numbers, "rules"
        )
    else:
        scores = desk.classifier.scores(issue)
        picks = desk.classifier.picks(scores)
        duplicate, confidence = similarity_decision(
            candidates, settings.duplicate_confident, settings.duplicate_propose
        )
        classical = gated(picks, duplicate, confidence, all_numbers, "classifier")
        use_model = config in PROMPT_OF and not (
            config == "cascade" and classifier_is_sure(picks, candidates, desk)
        )
        if not use_model:
            proposal = classical
        else:
            triager = desk.triagers.get(PROMPT_OF[config])
            shown = candidates[: settings.model_candidates]
            earlier = [desk.store.issue(c.number) for c in shown]
            result = triager.triage(issue, earlier) if triager is not None else None
            if result is None or result.triage is None:
                # No model answer: the classifier's answer stands and a person looks.
                proposal = gated(picks, duplicate, confidence, all_numbers, "classifier", True)
            else:
                answer = result.triage
                allowed = {c.number for c in shown} if triager.with_context else None
                labels = second_opinion(answer.labels, scores, desk.classifier.thresholds.propose)
                proposal = gated(
                    labels, answer.duplicate_of,
                    answer.duplicate_confidence if answer.duplicate_of is not None else None,
                    allowed, "model",
                )  # fmt: skip
                note = answer.note
                cost = result.stats.cost_usd
                model_seconds = result.stats.latency_s

    elapsed = time.perf_counter() - started
    if model_seconds and cost and elapsed < model_seconds:
        # A replayed answer returns at once; the log keeps the time the call took.
        elapsed += model_seconds
    prediction = Prediction(
        number=issue.number,
        created_at=issue.created_at,
        written_at=now,
        system_version=desk.version(config),
        config=config,
        proposal=proposal,
        candidates=[c.number for c in candidates],
        cost_usd=cost,
        latency_ms=int(elapsed * 1000),
        seen_labels=seen_labels,
        seen_closed=seen_closed,
    )
    return prediction, note
