"""Numbers for the two settings chosen on the development months.

`retrieval` (how earlier issues are searched) and the two similarity thresholds of
the no-model duplicate call are read off these tables. The held-out months are never
passed to this module.
"""

from __future__ import annotations

from .duplicates import MODES, Families, find_candidates, most_similar
from .index import IssueIndex
from .models import Issue, Outcome
from .truth import RESOLVED

# How deep to look when choosing how many earlier issues the model reads.
TUNE_DEPTHS = (1, 3, 5, 8, 10, 20)


def retrieval_table(
    index: IssueIndex,
    issues: list[Issue],
    outcomes: dict[int, Outcome],
    families: Families,
    limit: int = 20,
    fusion_k: int = 10,
) -> dict:
    """For each search mode: in how many duplicates the original is among the first k."""
    duplicates = [
        i for i in issues
        if (o := outcomes.get(i.number)) and o.duplicate and o.original_status == RESOLVED
    ]  # fmt: skip
    table = {"duplicates": len(duplicates), "modes": {}}
    for mode in MODES:
        hits = dict.fromkeys(TUNE_DEPTHS, 0)
        for issue in duplicates:
            found = [c.number for c in find_candidates(index, issue, limit, mode, fusion_k)]
            original = outcomes[issue.number].original
            for depth in TUNE_DEPTHS:
                if any(families.same(number, original) for number in found[:depth]):
                    hits[depth] += 1
        table["modes"][mode] = hits
    return table


def similarity_grid(
    index: IssueIndex,
    issues: list[Issue],
    outcomes: dict[int, Outcome],
    families: Families,
    mode: str,
    limit: int = 20,
    fusion_k: int = 10,
) -> list[dict]:
    """What each similarity threshold would flag: right, wrong issue, and new issues."""
    rows = []
    best = []
    for issue in issues:
        outcome = outcomes.get(issue.number)
        if outcome is None:
            continue
        closest = most_similar(find_candidates(index, issue, limit, mode, fusion_k))
        if closest is not None:
            best.append((closest.similarity, closest.number, outcome))
    for threshold in (0.80, 0.82, 0.84, 0.86, 0.88, 0.90, 0.92, 0.93, 0.94, 0.95, 0.96):
        flagged = [(n, o) for similarity, n, o in best if similarity >= threshold]
        right = sum(
            1 for n, o in flagged
            if o.duplicate and o.original is not None and families.same(n, o.original)
        )  # fmt: skip
        false = sum(1 for _, o in flagged if not o.duplicate)
        rows.append(
            {"threshold": threshold, "flagged": len(flagged), "right": right, "new_issues": false}
        )
    return rows
