"""The label set the desk may propose, with a definition for each label.

A definition starts from the repository's own label description. Many trackers leave
those empty (Godot's area and platform labels have none), so each definition also
carries what the training history shows: the words that mark the label and two
example titles. Nothing here is written by hand or by a model, which is what lets
`triagedesk init` build a taxonomy for any repository.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

from .config import Settings
from .models import Issue, LabelDefinition, LabelInfo, Taxonomy
from .text import problem_text, words

TERMS_PER_LABEL = 8
EXAMPLES_PER_LABEL = 2
# A term has to appear in this many issues of a label before it can describe it.
MIN_TERM_ISSUES = 5


def _issue_words(issue: Issue) -> set[str]:
    return set(words(f"{issue.title}\n{problem_text(issue.body)[:1500]}"))


def telling_terms(
    label_docs: list[set[str]], all_counts: Counter, total: int, own_name: str
) -> list[str]:
    """Words far more common in a label's issues than in the tracker as a whole."""
    counts: Counter = Counter()
    for doc in label_docs:
        counts.update(doc)
    size = len(label_docs)
    scored = []
    for term, inside in counts.items():
        if inside < MIN_TERM_ISSUES or term.isdigit() or len(term) < 3:
            continue
        lift = ((inside + 1) / (size + 2)) / ((all_counts[term] + 1) / (total + 2))
        if lift <= 1.5:
            continue
        scored.append((math.log(lift) * math.log(1 + inside), term))
    scored.sort(reverse=True)
    terms = [term for _, term in scored if term != own_name]
    return terms[:TERMS_PER_LABEL]


def build_taxonomy(
    settings: Settings, issues: list[Issue], label_infos: list[LabelInfo] | None = None
) -> Taxonomy:
    """Build the label set from training issues. `issues` must all predate the cut."""
    descriptions = {info.name: info.description for info in label_infos or []}
    docs = [_issue_words(issue) for issue in issues]
    all_counts: Counter = Counter()
    for doc in docs:
        all_counts.update(doc)

    definitions: list[LabelDefinition] = []
    dropped: dict[str, int] = {}
    for group in settings.groups:
        by_label: dict[str, list[int]] = {}
        for row, issue in enumerate(issues):
            for label in issue.labels:
                if label.startswith(group.prefix):
                    by_label.setdefault(label, []).append(row)
        for label in sorted(by_label, key=lambda name: (-len(by_label[name]), name)):
            rows = by_label[label]
            if len(rows) < group.min_issues:
                dropped[label] = len(rows)
                continue
            own_name = label.removeprefix(group.prefix).lower()
            alone = [
                issues[row].title
                for row in reversed(rows)
                if sum(1 for other in issues[row].labels if other.startswith(group.prefix)) == 1
            ]
            definitions.append(
                LabelDefinition(
                    name=label,
                    group=group.name,
                    description=descriptions.get(label, ""),
                    terms=telling_terms(
                        [docs[row] for row in rows], all_counts, len(docs), own_name
                    ),
                    examples=alone[:EXAMPLES_PER_LABEL],
                    training_issues=len(rows),
                )
            )
    return Taxonomy(
        repo=settings.repo, built_from_issues=len(issues), labels=definitions, dropped=dropped
    )


def public_form(taxonomy: Taxonomy) -> Taxonomy:
    """The taxonomy without example titles, which are third-party text."""
    return taxonomy.model_copy(
        update={"labels": [d.model_copy(update={"examples": []}) for d in taxonomy.labels]}
    )


def save(taxonomy: Taxonomy, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(taxonomy.model_dump(mode="json"), indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load(path: Path) -> Taxonomy:
    return Taxonomy.model_validate_json(path.read_text(encoding="utf-8"))


def detect_groups(label_infos: list[LabelInfo], min_labels: int = 3) -> dict[str, str]:
    """Guess label groups from shared prefixes such as "topic:" or "area/".

    Used by `init` on a repository nobody has configured. Returns prefix by group name.
    """
    prefixes: Counter = Counter()
    for info in label_infos:
        # The first ":" or "/" in the name ends the prefix. A hyphen does not: it
        # joins ordinary words ("needs-info", "good-first-issue").
        cut = min((p for p in (info.name.find(":"), info.name.find("/")) if p > 0), default=-1)
        if 0 < cut < len(info.name) - 1:
            prefixes[info.name[: cut + 1]] += 1
    groups = {}
    for prefix, count in prefixes.most_common():
        if count >= min_labels:
            groups[prefix.rstrip(":/").strip().lower().replace(" ", "_")] = prefix
    return groups
