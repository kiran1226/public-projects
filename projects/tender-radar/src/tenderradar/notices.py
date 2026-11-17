"""Turn a raw record from the feed into a typed Notice or Award.

The feed gives text as {language: value} and repeats values once per lot. This module
flattens both. A field the buyer left out stays None; nothing is guessed.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .models import Award, Lot, Notice, Winner


class ParseError(ValueError):
    """A record is missing something no notice can be without, or has the wrong shape."""


_WS = re.compile(r"\s+")
# Buyers sometimes type a contact address into a title or a description. It is removed
# here, at the one place every text passes through, so no prompt, list, recording or
# profile ever carries one.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
EMAIL_REMOVED = "[email removed]"


def _clean(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    return _WS.sub(" ", _EMAIL.sub(EMAIL_REMOVED, text)).strip()


def _required(raw: dict, key: str) -> Any:
    value = raw.get(key)
    if value in (None, "", [], {}):
        raise ParseError(f"record has no '{key}'")
    return value


def _date(value: Any, key: str) -> date:
    # Dates arrive as '2026-06-29+02:00' or '2026-06-29Z'.
    if not isinstance(value, str) or len(value) < 10:
        raise ParseError(f"'{key}' is not a date: {value!r}")
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise ParseError(f"'{key}' is not a date: {value!r}") from exc


def _languages(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _pick_language(value: Any, preferred: str | None) -> Any:
    """The value in the notice's own language, else the first language given."""
    by_language = _languages(value)
    if not by_language:
        return None
    if preferred and preferred in by_language:
        return by_language[preferred]
    return next(iter(by_language.values()))


def _text(value: Any, preferred: str | None) -> str:
    picked = _pick_language(value, preferred)
    if isinstance(picked, list):
        return _clean(picked[0]) if picked else ""
    return _clean(picked)


def _text_list(value: Any, preferred: str | None) -> list[str]:
    picked = _pick_language(value, preferred)
    if isinstance(picked, str):
        return [_clean(picked)]
    if isinstance(picked, list):
        return [_clean(item) for item in picked if isinstance(item, str)]
    return []


def _unique(values: Any) -> list[str]:
    seen: dict[str, None] = {}
    for item in values if isinstance(values, list) else []:
        if isinstance(item, str) and item:
            seen.setdefault(item, None)
    return list(seen)


def _first(values: Any) -> str | None:
    if isinstance(values, list):
        return next((v for v in values if isinstance(v, str) and v), None)
    return values if isinstance(values, str) and values else None


def _number(value: Any) -> float | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _language_code(raw: dict) -> str | None:
    code = _first(raw.get("official-language"))
    return code.lower() if code else None


def parse_notice(raw: dict) -> Notice:
    if not isinstance(raw, dict):
        raise ParseError("record is not an object")
    language = _language_code(raw)
    lot_titles = _text_list(raw.get("title-lot"), language)
    lot_descriptions = _text_list(raw.get("description-lot"), language)
    lots = [
        Lot(
            title=(lot_titles[i] if i < len(lot_titles) else None) or None,
            description=(lot_descriptions[i] if i < len(lot_descriptions) else None) or None,
        )
        for i in range(max(len(lot_titles), len(lot_descriptions)))
    ]
    deadlines = []
    for value in raw.get("deadline-receipt-tender-date-lot") or []:
        try:
            deadlines.append(_date(value, "deadline-receipt-tender-date-lot"))
        except ParseError:
            continue
    conditions = [
        c
        for c in _unique(_text_list(raw.get("selection-criterion-description-lot"), language))
        if c
    ]
    return Notice(
        publication_number=str(_required(raw, "publication-number")),
        publication_date=_date(_required(raw, "publication-date"), "publication-date"),
        notice_type=str(raw.get("notice-type") or ""),
        procedure_id=str(_required(raw, "procedure-identifier")),
        buyer_country=_first(raw.get("buyer-country")),
        buyer_name=_text(raw.get("buyer-name"), language) or None,
        language=language,
        title=_text(raw.get("title-proc"), language),
        description=_text(raw.get("description-proc"), language),
        lots=lots,
        cpv=_unique(raw.get("classification-cpv")),
        deadline=min(deadlines) if deadlines else None,
        estimated_value=_number(raw.get("estimated-value-proc")),
        currency=_first(raw.get("estimated-value-cur-proc")),
        procedure_type=_first(raw.get("procedure-type")),
        nature=_first(raw.get("contract-nature-main-proc")),
        conditions=conditions,
    )


def _winners(raw: dict) -> list[Winner]:
    """Winners of an award.

    Names, identifiers and countries arrive as three separate lists, each in its own
    order. In three months of awards, 812 of the 913 registration numbers that appeared
    with more than one name appeared with names of unrelated companies: pairing the
    lists by position attaches one company's number to another. So a name is paired
    with a number only when the award names exactly one winner and gives exactly one
    number. Otherwise the names stand alone.
    """
    names = list(dict.fromkeys(_text_list(raw.get("winner-name"), None)))
    identifiers = list(
        dict.fromkeys(i for i in (raw.get("winner-identifier") or []) if isinstance(i, str))
    )
    countries = [c for c in (raw.get("winner-country") or []) if isinstance(c, str)]
    sizes = [s for s in (raw.get("winner-size") or []) if isinstance(s, str)]
    only_country = countries[0] if len(set(countries)) == 1 else None
    if len(names) == 1 and len(identifiers) == 1:
        only_size = sizes[0] if len(set(sizes)) == 1 else None
        return [
            Winner(name=names[0], identifier=identifiers[0], country=only_country, size=only_size)
        ]
    if names:
        return [Winner(name=name, country=only_country) for name in names]
    return [Winner(identifier=identifier, country=only_country) for identifier in identifiers]


def parse_award(raw: dict) -> Award:
    if not isinstance(raw, dict):
        raise ParseError("record is not an object")
    return Award(
        publication_number=str(_required(raw, "publication-number")),
        publication_date=_date(_required(raw, "publication-date"), "publication-date"),
        procedure_id=str(_required(raw, "procedure-identifier")),
        buyer_country=_first(raw.get("buyer-country")),
        buyer_name=_text(raw.get("buyer-name"), None) or None,
        title=_text(raw.get("title-proc"), None),
        description=_text(raw.get("description-proc"), None),
        cpv=_unique(raw.get("classification-cpv")),
        winners=_winners(raw),
        value=_number(raw.get("result-value-notice")),
        currency=_first(raw.get("result-value-cur-notice")),
    )
