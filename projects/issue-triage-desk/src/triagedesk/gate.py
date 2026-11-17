"""The gate: every answer is checked in code before it becomes a proposal.

The model's answer is a suggestion. These rules decide what is kept and when a person
has to look, and they hold whatever the issue text told the model to do.
"""

from __future__ import annotations

from collections.abc import Callable

from .models import CONFIDENCE_ORDER, Confidence, LabelPick, Proposal, Taxonomy

# Gate rules, by name.
LABEL_NOT_IN_TAXONOMY = "LABEL_NOT_IN_TAXONOMY"
DUPLICATE_NOT_A_CANDIDATE = "DUPLICATE_NOT_A_CANDIDATE"
DUPLICATE_NOT_OLDER = "DUPLICATE_NOT_OLDER"
TOO_MANY_LABELS = "TOO_MANY_LABELS"

# Reasons a person has to look.
NO_CONFIDENT_LABEL = "no_confident_{group}"
UNCERTAIN_LABEL = "uncertain_label"
POSSIBLE_DUPLICATE = "possible_duplicate"
GATE_CHANGED_ANSWER = "gate_changed_answer"
MODEL_UNAVAILABLE = "model_unavailable"

# More labels of one kind than this is a sign the answer is a guess.
MAX_PER_GROUP = 3


def gate(
    labels: list[LabelPick],
    duplicate_of: int | None,
    duplicate_confidence: Confidence | None,
    *,
    taxonomy: Taxonomy,
    issue_number: int,
    allowed_duplicates: set[int] | None,
    is_older: Callable[[int], bool],
    primary_group: str,
    source: str,
    model_unavailable: bool = False,
) -> Proposal:
    """Turn an answer into a proposal.

    `allowed_duplicates` is the set of earlier issues that were retrieved and shown.
    None means the answer came from memory (the "model alone" configuration); then
    the issue only has to exist and be older.
    """
    fired: list[str] = []

    kept: dict[str, LabelPick] = {}
    for pick in labels:
        if taxonomy.group_of(pick.name) is None:
            fired.append(LABEL_NOT_IN_TAXONOMY)
            continue
        earlier = kept.get(pick.name)
        if (
            earlier is None
            or CONFIDENCE_ORDER[pick.confidence] > CONFIDENCE_ORDER[earlier.confidence]
        ):
            kept[pick.name] = pick
    picks = sorted(kept.values(), key=lambda p: (-CONFIDENCE_ORDER[p.confidence], p.name))
    per_group: dict[str, int] = {}
    limited: list[LabelPick] = []
    for pick in picks:
        group = taxonomy.group_of(pick.name)
        per_group[group] = per_group.get(group, 0) + 1
        if per_group[group] > MAX_PER_GROUP:
            fired.append(TOO_MANY_LABELS)
            continue
        limited.append(pick)

    if duplicate_of is not None and duplicate_confidence == "low":
        # A guess is not a proposal, for a duplicate as for a label.
        duplicate_of = None
    if duplicate_of is not None:
        if duplicate_of == issue_number or not is_older(duplicate_of):
            fired.append(DUPLICATE_NOT_OLDER)
            duplicate_of = None
        elif allowed_duplicates is not None and duplicate_of not in allowed_duplicates:
            fired.append(DUPLICATE_NOT_A_CANDIDATE)
            duplicate_of = None
    if duplicate_of is None:
        duplicate_confidence = None

    person: list[str] = []
    if model_unavailable:
        person.append(MODEL_UNAVAILABLE)
    if not any(
        taxonomy.group_of(p.name) == primary_group and p.confidence == "high" for p in limited
    ):
        person.append(NO_CONFIDENT_LABEL.format(group=primary_group))
    if any(p.confidence == "medium" for p in limited):
        person.append(UNCERTAIN_LABEL)
    if duplicate_of is not None and duplicate_confidence != "high":
        person.append(POSSIBLE_DUPLICATE)
    if fired:
        person.append(GATE_CHANGED_ANSWER)

    return Proposal(
        labels=limited,
        duplicate_of=duplicate_of,
        duplicate_confidence=duplicate_confidence,
        person=person,
        gate=list(dict.fromkeys(fired)),
        source=source,
    )
