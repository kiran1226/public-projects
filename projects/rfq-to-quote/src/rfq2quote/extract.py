"""Extract a typed RFQ from document text.

Two implementations behind one interface:

- `LLMExtractor`: Claude with structured output. Handles prose, mixed languages,
  corrections and unit conversion.
- `RuleExtractor`: regular expressions only. It is the baseline the LLM has to
  beat, and it shows where rules are enough (clean tables) and where they are not.
"""

from __future__ import annotations

import hashlib
import re
from importlib import resources
from typing import Protocol

from .intake import Document
from .llm import LLMProvider
from .models import RFQ, LineItem, Usage


def load_prompt(name: str) -> str:
    return resources.files("rfq2quote.prompts").joinpath(f"{name}.md").read_text(encoding="utf-8")


def record_call(usage: Usage, stats) -> None:
    usage.llm_calls += 1
    usage.input_tokens += stats.input_tokens
    usage.output_tokens += stats.output_tokens
    usage.cost_usd += stats.cost_usd
    usage.latency_s += stats.latency_s


class Extractor(Protocol):
    name: str

    def extract(self, doc: Document, usage: Usage) -> RFQ: ...


class LLMExtractor:
    def __init__(self, provider: LLMProvider, prompt_version: str = "v2"):
        self.provider = provider
        self.name = f"llm-{prompt_version}"
        self.system = load_prompt(f"extract_{prompt_version}")
        self.prompt_sha256 = hashlib.sha256(self.system.encode("utf-8")).hexdigest()

    def extract(self, doc: Document, usage: Usage) -> RFQ:
        system = self.system.replace("{received_date}", doc.received_date or "an unknown date")
        user = (
            f'<document type="{doc.kind}">\n{doc.text}\n</document>\n\n'
            "Extract the request for quotation from the document above."
        )
        rfq, stats = self.provider.structured(system=system, user=user, schema=RFQ)
        record_call(usage, stats)
        return rfq


# --- rule-based baseline ------------------------------------------------------

_UNITS = {
    "m": ("m", 1), "mtr": ("m", 1), "meter": ("m", 1), "metre": ("m", 1), "metres": ("m", 1),
    "meters": ("m", 1), "lfm": ("m", 1), "km": ("m", 1000),
    "stk": ("pcs", 1), "stück": ("pcs", 1), "st": ("pcs", 1), "pc": ("pcs", 1),
    "pcs": ("pcs", 1), "piece": ("pcs", 1), "pieces": ("pcs", 1), "ea": ("pcs", 1),
    "x": ("pcs", 1),
}  # fmt: skip
_UNIT_ALT = "|".join(sorted(_UNITS, key=len, reverse=True))
_NUM = r"\d[\d.,]*\d|\d"
_INLINE = re.compile(
    rf"(?<![\w.,/])({_NUM})\s*({_UNIT_ALT})\b\.?\s+(?:of\s+|vom\s+)?"
    r"(\S.{2,}?)(?=,(?!\d)|;|\s+(?:and|und|sowie|as well as)\s|\.\s|\.?$)",
    re.IGNORECASE,
)
_QTY_UNIT_CELL = re.compile(rf"^({_NUM})\s*({_UNIT_ALT})\.?$", re.IGNORECASE)
_NUM_CELL = re.compile(rf"^(?:{_NUM})$")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_COMPANY = re.compile(
    r"([A-ZÄÖÜ][\w&.-]*(?: [\w&.-]+){0,3} (?:GmbH(?: & Co\. KG)?|AG|KG|UG|Ltd\.?|e\.K\.))"
)
_DATE_DE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTHS = {
    name: n
    for n, names in enumerate(
        [
            "january januar",
            "february februar",
            "march märz",
            "april",
            "may mai",
            "june juni",
            "july juli",
            "august",
            "september",
            "october oktober",
            "november",
            "december dezember",
        ],
        start=1,
    )  # fmt: skip
    for name in names.split()
}
_DATE_WORDS = re.compile(rf"\b(\d{{1,2}})\.? ({'|'.join(_MONTHS)}) (\d{{4}})\b", re.IGNORECASE)


def parse_number(raw: str) -> float | None:
    """Parse "1.500", "5,000", "2,5" and "1200" without knowing the locale."""
    raw = raw.strip()
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw):
        return float(re.sub(r"[.,]", "", raw))
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def _item(qty_raw: str, unit_raw: str, description: str) -> LineItem | None:
    qty = parse_number(qty_raw)
    unit, factor = _UNITS[unit_raw.lower()]
    if qty is None or not description.strip():
        return None
    return LineItem(description=description.strip(" |;,-"), quantity=qty * factor, unit=unit)


def _from_table_row(line: str) -> LineItem | None:
    cells = [c.strip() for c in line.split("|")]
    text_cells = [
        c
        for c in cells
        if re.search(r"[A-Za-zÄÖÜäöü]", c)
        and c.lower().rstrip(".") not in _UNITS
        and not _QTY_UNIT_CELL.match(c)
    ]
    if len(cells) < 2 or not text_cells:
        return None
    description = max(text_cells, key=len)
    for i, cell in enumerate(cells):
        combined = _QTY_UNIT_CELL.match(cell)
        if combined:
            return _item(combined.group(1), combined.group(2), description)
        nxt = cells[i + 1].lower().rstrip(".") if i + 1 < len(cells) else ""
        if _NUM_CELL.match(cell) and nxt in _UNITS:
            return _item(cell, nxt, description)
    return None


class RuleExtractor:
    name = "rules"

    def extract(self, doc: Document, usage: Usage) -> RFQ:
        body = doc.text
        items: list[LineItem] = []
        for line in body.splitlines():
            line = line.strip().lstrip(">").strip()
            if not line or line.lower().startswith(("from:", "to:", "date:", "subject:")):
                continue
            if "|" in line:
                found = [_from_table_row(line)]
            else:
                found = [_item(*m.groups()) for m in _INLINE.finditer(line)]
            items += [item for item in found if item]

        company = _COMPANY.search(body)
        emails = _EMAIL.findall(body)
        date = None
        text_after_headers = body.split("\n\n", 1)[-1]
        if m := _DATE_DE.search(text_after_headers):
            date = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
        elif m := _DATE_ISO.search(text_after_headers):
            date = m.group(0)
        elif m := _DATE_WORDS.search(text_after_headers):
            date = f"{m.group(3)}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
        return RFQ(
            customer_name=company.group(1) if company else doc.sender_name,
            customer_email=doc.sender_email or (emails[0].lower() if emails else None),
            delivery_date=date,
            delivery_location=None,
            line_items=items,
            missing_info=[],
        )
