"""The rules baseline: a label is proposed when its keyword is in the issue.

This is what a triage lead writes in an afternoon, and what the classifier and the
model have to beat. A keyword is the label's own name ("gdscript" for
`topic:gdscript`) plus any words listed for it in the configuration.
"""

from __future__ import annotations

import re

from .config import Settings
from .models import LabelPick, Taxonomy
from .text import problem_text

# A keyword in the title is a strong sign; in the body it has to appear this often.
BODY_MENTIONS = 2


class Rules:
    def __init__(self, settings: Settings, taxonomy: Taxonomy):
        self._patterns: dict[str, re.Pattern] = {}
        for definition in taxonomy.labels:
            prefix = next((g.prefix for g in settings.groups if g.name == definition.group), "")
            keywords = {definition.name.removeprefix(prefix).lower()}
            keywords.update(settings.keywords.get(definition.name, ()))
            alternatives = "|".join(re.escape(k) for k in sorted(keywords, key=len, reverse=True))
            # A keyword must stand alone: "web" must not match "webp".
            self._patterns[definition.name] = re.compile(
                rf"(?<![\w.#+-])(?:{alternatives})(?![\w#+-])", re.IGNORECASE
            )

    def labels(self, title: str, body: str) -> list[LabelPick]:
        picks = []
        text = problem_text(body)
        for name, pattern in self._patterns.items():
            if pattern.search(title):
                picks.append(LabelPick(name=name, confidence="high"))
            elif len(pattern.findall(text)) >= BODY_MENTIONS:
                picks.append(LabelPick(name=name, confidence="medium"))
        return picks
