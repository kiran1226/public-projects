"""Tier-1 features: only what the stream event itself carries.

No request is made to compute them, so tier 1 runs on every edit: about 50
milliseconds for a batch of whatever has arrived. Tags are left out on purpose: the
recent-changes list has them, the live stream event does not, and a feature the
live run cannot see would make the backtest flatter the system.
"""

from __future__ import annotations

import math
import re

from .models import Edit
from .scrub import is_revert_summary

FEATURES = (
    "delta",
    "abs_delta_log",
    "old_len_log",
    "new_len_log",
    "shrink_share",
    "minor",
    "temp",
    "comment_len",
    "comment_empty",
    "comment_section_only",
    "comment_is_revert",
    "comment_has_link",
    "comment_upper_share",
    "comment_auto",
    "hour",
    "weekday",
    "parent_gap_log",
    "title_len",
    "title_has_year",
    "title_has_brackets",
)

_SECTION = re.compile(r"/\*.*?\*/")
_YEAR = re.compile(r"\b(1[5-9]|20)\d\d\b")
# Summaries that tools write for the editor.
_AUTO = re.compile(r"\b(AWB|Twinkle|HotCat|RedWarn|Huggle|script|bot|autowikibrowser)\b", re.I)


def _log(value: float) -> float:
    return math.log1p(max(0.0, value))


def features(edit: Edit) -> list[float]:
    """One row for the classifier, in the order of FEATURES."""
    comment = edit.comment
    body = _SECTION.sub("", comment).strip()
    letters = [c for c in body if c.isalpha()]
    upper = sum(1 for c in letters if c.isupper()) / len(letters) if letters else 0.0
    delta = edit.delta
    return [
        float(max(-5000, min(5000, delta))),
        _log(abs(delta)),
        _log(edit.old_len),
        _log(edit.new_len),
        (-delta / edit.old_len) if delta < 0 and edit.old_len else 0.0,
        float(edit.minor),
        float(edit.temp),
        float(min(len(body), 300)),
        float(not comment.strip()),
        float(bool(comment.strip()) and not body),
        float(is_revert_summary(comment)),
        float("http" in body or "[[" in body),
        upper,
        float(bool(_AUTO.search(body))),
        float(edit.timestamp.hour),
        float(edit.timestamp.weekday()),
        # Revision ids count up across the wiki: a small gap to the parent means the
        # page was edited moments ago.
        _log(edit.rev_id - edit.parent_id) if edit.parent_id else 25.0,
        float(min(len(edit.title), 80)),
        float(bool(_YEAR.search(edit.title))),
        float("(" in edit.title),
    ]
