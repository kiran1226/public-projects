"""What an edit changed: the lines that differ between a revision and its parent.

The wiki API can compute a diff, one request per edit. Reading the two revision
texts instead, 50 revisions to a request, and comparing them here costs a twenty-fifth
of the requests, which is what a polite client can afford at this volume.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable

from .models import Change, Edit
from .store import Store
from .wiki import BATCH, WikiClient

# A line longer than this is shown around the part that changed.
LINE_CHARS = 400
CONTEXT = 120
# Enclose the part of a changed line that differs from its other version.
OPEN, CLOSE = "⟦", "⟧"


def mark_difference(old: str, new: str) -> tuple[str, str]:
    """Two versions of one line, each with the part that differs enclosed in ⟦ ⟧.

    A long line is also cut down to that part and some context. Without the marks a
    reader has to find a few changed words in a paragraph, and a model reading
    "removed" and "added" lines that look alike tends to judge the words it notices,
    not which side they are on.
    """
    limit = min(len(old), len(new))
    start = 0
    while start < limit and old[start] == new[start]:
        start += 1
    end = 0
    while end < limit - start and old[-1 - end] == new[-1 - end]:
        end += 1
    # Widen the differing part to whole words: "1⟦8⟧87" helps nobody.
    while start > 0 and not old[start - 1].isspace():
        start -= 1
    while end > 0 and not old[len(old) - end].isspace():
        end -= 1
    lo = max(0, start - CONTEXT) if max(len(old), len(new)) > LINE_CHARS else 0

    def window(text: str) -> str:
        stop = len(text) - end
        hi = min(len(text), stop + CONTEXT) if lo or len(text) > LINE_CHARS else len(text)
        middle = text[start:stop][: LINE_CHARS * 2]
        marked = text[lo:start] + OPEN + middle + CLOSE + text[stop:hi]
        return ("… " if lo else "") + marked + (" …" if hi < len(text) else "")

    return window(old), window(new)


def mark_pairs(lines: list[str]) -> list[str]:
    """Add the marks to rendered lines where a removed line is followed by its replacement.

    Used for changes that were written by hand (the invented fixture). A line that
    already carries a mark is left alone.
    """
    out = list(lines)
    for i in range(len(out) - 1):
        first, second = out[i], out[i + 1]
        n1, s1, t1 = first.split(" ", 2) if first.count(" ") >= 2 else ("", "", "")
        n2, s2, t2 = second.split(" ", 2) if second.count(" ") >= 2 else ("", "", "")
        alone_before = i == 0 or out[i - 1].split(" ", 2)[1:2] != ["-"]
        alone_after = i + 2 >= len(out) or out[i + 2].split(" ", 2)[1:2] != ["+"]
        if (s1, s2) == ("-", "+") and alone_before and alone_after and OPEN not in t1 + t2:
            a, b = mark_difference(t1.removeprefix("… "), t2.removeprefix("… "))
            lead1 = "… " if t1.startswith("… ") and not a.startswith("… ") else ""
            lead2 = "… " if t2.startswith("… ") and not b.startswith("… ") else ""
            out[i], out[i + 1] = f"{n1} - {lead1}{a}", f"{n2} + {lead2}{b}"
    return out


def compute_change(rev_id: int, old: str | None, new: str | None, limit: int = 1800) -> Change:
    """The lines an edit removed and added, numbered, cut at `limit` characters."""
    if old is None or new is None:
        return Change(rev_id=rev_id, available=False)
    old_lines, new_lines = old.splitlines(), new.splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    out: list[str] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        removed, added = old_lines[i1:i2], new_lines[j1:j2]
        if op == "replace" and len(removed) == len(added):
            pairs = [mark_difference(a, b) for a, b in zip(removed, added, strict=True)]
            removed, added = [p[0] for p in pairs], [p[1] for p in pairs]
        out.extend(f"- {line[: LINE_CHARS * 2]}" for line in removed if line.strip())
        out.extend(f"+ {line[: LINE_CHARS * 2]}" for line in added if line.strip())
    lines: list[str] = []
    used = 0
    truncated = False
    for number, line in enumerate(out, start=1):
        numbered = f"{number} {line}"
        if used + len(numbered) + 1 > limit:
            room = limit - used
            if room > 40 and not lines:
                lines.append(numbered[:room])
            truncated = True
            break
        lines.append(numbered)
        used += len(numbered) + 1
    return Change(rev_id=rev_id, lines=lines, truncated=truncated)


def fetch_changes(
    client: WikiClient,
    store: Store,
    edits: list[Edit],
    limit: int = 1800,
    progress: Callable[[str], None] = lambda line: None,
) -> int:
    """Compute and store the change of each edit that has none yet. Returns how many."""
    have = store.change_ids()
    todo = [e for e in edits if e.rev_id not in have]
    done = 0
    step = BATCH // 2  # each edit needs two revisions
    for start in range(0, len(todo), step):
        chunk = todo[start : start + step]
        ids = sorted({e.rev_id for e in chunk} | {e.parent_id for e in chunk if e.parent_id})
        texts: dict[int, str | None] = {}
        for at in range(0, len(ids), BATCH):
            texts.update(client.revision_texts(ids[at : at + BATCH]))
        for edit in chunk:
            old = texts.get(edit.parent_id) if edit.parent_id else ""
            store.set_change(compute_change(edit.rev_id, old, texts.get(edit.rev_id), limit))
            done += 1
        progress(f"{done} of {len(todo)} changes fetched")
    return done
