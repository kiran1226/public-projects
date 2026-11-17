"""Issue text: cleaning, the template's sections, and the passage that matches.

Issue text is third-party content. These functions read it; nothing here writes it
anywhere but the local store.
"""

from __future__ import annotations

import re

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)|<img[^>]*>|<video[^>]*>.*?</video>", re.DOTALL)
_ATTACHMENT_URL = re.compile(r"https://github\.com/(?:user-attachments|[\w.-]+/[\w.-]+/assets)\S+")
_HEADING = re.compile(r"^#{2,4}\s+(.+?)\s*$", re.MULTILINE)
_WS = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")
# A word may hold "+", "#", "." or "-" inside it ("c#", "forward+", "4.3-stable").
_WORD = re.compile(r"\w[\w+#.-]*[\w+#]|\w")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")

# Longest fenced block kept. Logs and stack traces carry signal in their first lines.
CODE_KEEP = 400
PROMPT_BODY_CHARS = 3000
SEARCH_CHARS = 1500
PASSAGE_CHARS = 240

# Sections of an issue template that describe the reporter's setup, not the problem.
_SETUP_HEADINGS = ("version", "system", "environment", "minimal reproduction", "mrp")

STOPWORDS = frozenset(
    """a an and are as at be been but by can could did do does for from had has have how i if in
    into is it its just me my no not of on or our so than that the their then there these they
    this to was we were what when where which while who why will with would you your also any
    after before when using use used get got see seems seem like one two only still very more
    most some same other should about above below up down out over under again new issue bug
    problem expected actual result results steps step reproduce description""".split()
)


def clean(body: str) -> str:
    """Body text without comments, embedded media and most of each code block."""

    def shorten(match: re.Match) -> str:
        block = match.group(0)
        return block if len(block) <= CODE_KEEP else block[:CODE_KEEP] + "\n```"

    text = _HTML_COMMENT.sub(" ", body or "")
    text = _IMAGE.sub(" ", text)
    text = _ATTACHMENT_URL.sub(" ", text)
    text = _CODE_FENCE.sub(shorten, text)
    text = _WS.sub(" ", text.replace("\r\n", "\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


def sections(body: str) -> dict[str, str]:
    """The body split at its headings. Text before the first heading is under ''."""
    text = clean(body)
    parts: dict[str, str] = {}
    position = 0
    name = ""
    for match in _HEADING.finditer(text):
        chunk = text[position : match.start()].strip()
        if chunk:
            parts[name] = (parts.get(name, "") + "\n" + chunk).strip()
        name = match.group(1).strip().lower()
        position = match.end()
    tail = text[position:].strip()
    if tail:
        parts[name] = (parts.get(name, "") + "\n" + tail).strip()
    return parts


def problem_text(body: str) -> str:
    """The parts of the body that describe the problem, without the setup sections."""
    parts = sections(body)
    kept = [
        chunk
        for heading, chunk in parts.items()
        if not any(word in heading for word in _SETUP_HEADINGS)
    ]
    return "\n".join(kept) if kept else clean(body)


def search_text(title: str, body: str) -> str:
    """What the index stores and searches: the title and the start of the problem."""
    return f"{title.strip()}\n{problem_text(body)[:SEARCH_CHARS]}".strip()


def classifier_text(title: str, body: str) -> str:
    """What the classifier reads. The title counts twice; the setup sections stay in."""
    return f"{title}\n{title}\n{clean(body)[:PROMPT_BODY_CHARS]}"


def prompt_body(body: str) -> str:
    text = clean(body)
    if len(text) <= PROMPT_BODY_CHARS:
        return text
    return text[:PROMPT_BODY_CHARS] + "\n[cut]"


def words(text: str) -> list[str]:
    """Lower-case word tokens without stopwords, in order."""
    return [w for w in _WORD.findall(text.lower()) if w not in STOPWORDS and len(w) > 1]


def matching_passage(query: str, body: str, title: str = "") -> str:
    """The sentence of an earlier issue that shares the most words with the new one.

    Computed, not written by a model, so it is always text that is in the issue.
    """
    wanted = set(words(query))
    best = ""
    best_score = 0.0
    for sentence in _SENTENCE_END.split(problem_text(body)):
        sentence = sentence.strip(" -*>#")
        if len(sentence) < 12:
            continue
        found = set(words(sentence))
        if not found:
            continue
        shared = len(found & wanted)
        # Favour sentences that are mostly shared words over long ones that share a few.
        score = shared / (len(found) ** 0.5)
        if score > best_score:
            best, best_score = sentence, score
    passage = best or title.strip()
    if len(passage) > PASSAGE_CHARS:
        passage = passage[: PASSAGE_CHARS - 2].rsplit(" ", 1)[0] + " …"
    return passage
