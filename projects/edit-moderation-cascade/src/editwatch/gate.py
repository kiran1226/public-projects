"""The gate: the model's three fields are checked in code before they count.

If a check fails, the model's answer is dropped and the edit keeps its tier-1 score.
Nothing an edit says can push it down the queue through a malformed answer.
"""

from __future__ import annotations

from .config import CATEGORIES
from .models import Change, Tier2Answer

RISK_OUT_OF_RANGE = "RISK_OUT_OF_RANGE"
CATEGORY_NOT_IN_LIST = "CATEGORY_NOT_IN_LIST"
RANGE_OUTSIDE_CHANGE = "RANGE_OUTSIDE_CHANGE"
RANGE_WITHOUT_CATEGORY = "RANGE_WITHOUT_CATEGORY"
CHANGE_UNAVAILABLE = "CHANGE_UNAVAILABLE"

# A rule in this set drops the model's answer. The others repair one field.
REJECTS = {RISK_OUT_OF_RANGE, CATEGORY_NOT_IN_LIST, RANGE_OUTSIDE_CHANGE, CHANGE_UNAVAILABLE}


def check(answer: Tier2Answer, change: Change) -> tuple[Tier2Answer | None, list[str]]:
    """The answer if it may count, and the rules that fired."""
    fired: list[str] = []
    if not change.available:
        return None, [CHANGE_UNAVAILABLE]
    if not 0 <= answer.risk <= 100:
        fired.append(RISK_OUT_OF_RANGE)
    if answer.category not in CATEGORIES:
        fired.append(CATEGORY_NOT_IN_LIST)
    first, last = answer.first_line, answer.last_line
    has_range = (first, last) != (0, 0)
    if has_range and not (1 <= first <= last <= len(change.lines)):
        fired.append(RANGE_OUTSIDE_CHANGE)
    if REJECTS & set(fired):
        return None, fired
    if has_range and answer.category == "none":
        # A marked passage with no problem named: keep the risk, drop the marking.
        fired.append(RANGE_WITHOUT_CATEGORY)
        answer = answer.model_copy(update={"first_line": 0, "last_line": 0})
    return answer, fired
