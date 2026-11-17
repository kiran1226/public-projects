"""Edit summaries name people. This removes the names before anything is stored.

A summary of a revert reads "Undid revision 123 by SomeAccount (talk)" or "Reverted
edits by SomeAccount to last version by Other". Those are public account names in
public text, and the desk still has no use for them: it judges edits, not editors.
"""

from __future__ import annotations

import re

REMOVED = "[name]"

_USER_LINK = re.compile(
    r"\[\[\s*(?:Special:Contrib(?:ution)?s/|User(?:[ _]talk)?:)[^\]]*\]\]", re.IGNORECASE
)
_TALK_LINK = re.compile(r"\(\s*\[\[\s*User[ _]talk:[^\]]*\]\]\s*\)", re.IGNORECASE)
# In a revert summary, what follows "by" is an account name, which may have spaces.
# It ends at "(talk)", at "to last version", at punctuation or at the end.
_BY_NAME = re.compile(
    r"\b(by|to last (?:version|revision) by)\s+"
    r"(?!the\b|a\b|an\b|me\b|my\b|consensus\b|mistake\b|accident\b|request\b)"
    r".+?(?=\s+\(|\s+to\s+(?:the\s+)?(?:last|rev)|[:;,.]|$)",
    re.IGNORECASE,
)
# The "(talk)" that follows a name in a revert summary, once the name is gone.
_TALK_PLAIN = re.compile(r"\s*\((?:talk|contribs)(?:\s*\|\s*(?:talk|contribs))?\)", re.IGNORECASE)
_MENTION = re.compile(r"(?<![\w/])@[\w.-]+")
_TEMP_NAME = re.compile(r"~\d{4}-\d+(?:-\d+)*")
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
# A full address of eight groups, or a shortened one, which contains "::".
_IPV6 = re.compile(
    r"(?<![\w:])(?:(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}"
    r"|(?:[0-9a-f]{1,4}:){1,6}:(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){0,5})?)(?![\w:])",
    re.IGNORECASE,
)
_REVERT = re.compile(
    r"\b(undid|undo|revert(?:ed|ing)?|rv[vt]?|rollback|restor(?:ed|ing)|reverting)\b",
    re.IGNORECASE,
)


def is_revert_summary(comment: str) -> bool:
    """True if the summary says the edit undoes another one."""
    return bool(_REVERT.search(comment or ""))


def scrub_comment(comment: str) -> str:
    """Remove account names, temporary-account names and IP addresses from a summary."""
    text = _TALK_LINK.sub("", comment or "")
    text = _USER_LINK.sub(REMOVED, text)
    text = _TEMP_NAME.sub(REMOVED, text)
    text = _IPV4.sub(REMOVED, text)
    text = _IPV6.sub(REMOVED, text)
    text = _MENTION.sub(REMOVED, text)
    if is_revert_summary(text):
        # In a revert summary the word after "by" is an account name.
        text = _BY_NAME.sub(lambda m: f"{m.group(1)} {REMOVED}", text)
        text = _TALK_PLAIN.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def summary_for_model(comment: str) -> str:
    """What the model is told about the summary. Nothing, if the edit is itself a revert."""
    if is_revert_summary(comment):
        return "(this edit undoes an earlier edit; its summary is not shown)"
    return scrub_comment(comment)[:300]


def is_temp_account(name: str | None) -> bool:
    """A temporary account's name starts with a tilde and a year: "~2026-12345-6"."""
    return bool(name) and bool(_TEMP_NAME.fullmatch(name.strip()))
