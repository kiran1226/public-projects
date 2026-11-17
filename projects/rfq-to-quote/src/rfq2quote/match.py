"""Match an extracted line item to a catalog SKU.

Retrieval (SQLite FTS5) narrows ~200 SKUs to a handful; the matcher then decides.
`LLMMatcher` lets Claude choose or decline. `TopHitMatcher` is the no-LLM baseline.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

from .catalog import Catalog, normalize, query_tokens
from .extract import load_prompt, record_call
from .llm import LLMProvider
from .models import LineItem, MatchDecision, MatchedLine, Usage


class Matcher(Protocol):
    name: str

    def match(self, item: LineItem, usage: Usage) -> MatchedLine: ...


def no_match(item: LineItem, reason: str, candidates: list[str] | None = None) -> MatchedLine:
    return MatchedLine(
        item=item, sku=None, confidence="low", reason=reason, candidates=candidates or []
    )


def _quantity_text(item: LineItem) -> str:
    if item.quantity is None:
        return "not stated"
    return f"{item.quantity:g} {item.unit or '(unit not stated)'}"


class TopHitMatcher:
    """Take the best search hit. Confident only if every number in the request is in the SKU."""

    name = "top-hit"

    def __init__(self, catalog: Catalog, limit: int = 8):
        self.catalog = catalog
        self.limit = limit

    def match(self, item: LineItem, usage: Usage) -> MatchedLine:
        hits = self.catalog.search(item.description, self.limit)
        if not hits:
            return no_match(item, "No catalog entry shares any term with this line.")
        best = hits[0][0]
        sku_tokens = set(self.catalog.search_text(best.sku).split())
        numeric = [t for t in query_tokens(item.description) if re.search(r"\d", t)]
        words = [t for t in query_tokens(item.description) if not re.search(r"\d", t)]
        numbers_ok = all(t in sku_tokens for t in numeric)
        word_ok = any(t in sku_tokens for t in words)
        confidence = "high" if numbers_ok and word_ok and numeric else "low"
        return MatchedLine(
            item=item,
            sku=best.sku,
            confidence=confidence,
            reason=f"Top search hit: {best.description}",
            candidates=[s.sku for s, _ in hits],
        )


class LLMMatcher:
    def __init__(self, catalog: Catalog, provider: LLMProvider, limit: int = 8):
        self.catalog = catalog
        self.provider = provider
        self.limit = limit
        self.name = "llm-match"
        self.system = load_prompt("match_v1")

    def match(self, item: LineItem, usage: Usage) -> MatchedLine:
        hits = self.catalog.search(item.description, self.limit)
        if not hits:
            # Nothing retrieved: no point paying for a model call.
            return no_match(item, "No catalog entry shares any term with this line.")
        candidates = [
            {
                "sku": s.sku,
                "description": s.description,
                "also_known_as": s.keywords,
                "unit": s.unit,
            }
            for s, _ in hits
        ]
        user = (
            f"Customer line: {item.description!r}\n"
            f"Requested quantity: {_quantity_text(item)}\n\n"
            f"Candidates:\n{json.dumps(candidates, ensure_ascii=False, indent=1)}"
        )
        decision, stats = self.provider.structured(
            system=self.system, user=user, schema=MatchDecision
        )
        record_call(usage, stats)
        ids = [c["sku"] for c in candidates]
        if decision.sku is not None and decision.sku not in ids:
            # Guard against an invented SKU: treat it as no match.
            return no_match(item, f"Model returned unknown SKU {decision.sku!r}.", ids)
        return MatchedLine(
            item=item,
            sku=decision.sku,
            confidence=decision.confidence if decision.sku else "low",
            reason=decision.reason,
            candidates=ids,
        )


__all__ = ["LLMMatcher", "Matcher", "TopHitMatcher", "normalize"]
