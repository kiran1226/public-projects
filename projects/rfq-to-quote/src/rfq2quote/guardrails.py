"""Deterministic guardrails: decide whether a draft quote may go out without a human.

Every rule is plain code with a named reason, so a reviewer sees why a quote was
held and the business can change a threshold without touching a prompt. All rules
always run; a held quote lists every reason, not only the first.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

from .catalog import Catalog, sizes
from .models import RFQ, Flag, MatchedLine, Quote, Route, SourceFacts


@dataclass(frozen=True)
class Policy:
    margin_floor_pct: float = 12.0
    high_value_eur: float = 50_000.0
    # Our own mail domains: a quote must never be addressed to a colleague.
    own_domains: tuple[str, ...] = ("helios-supply.example",)
    # Assisted mode: nothing is auto-approved, every quote is reviewed.
    assisted: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def _review(code: str, detail: str) -> Flag:
    return Flag(code=code, severity="review", detail=detail)


def _domain(address: str | None) -> str:
    return (address or "").rsplit("@", 1)[-1].lower()


def evaluate(
    rfq: RFQ,
    matches: list[MatchedLine],
    quote: Quote,
    catalog: Catalog,
    as_of: date,
    policy: Policy | None = None,
    facts: SourceFacts | None = None,
) -> list[Flag]:
    policy = policy or Policy()
    flags: list[Flag] = []

    # --- is there something to quote, and someone to quote to? ---------------
    if not matches:
        flags.append(_review("NO_LINE_ITEMS", "No products found; a person should read this."))
    if not rfq.customer_name:
        flags.append(_review("MISSING_CUSTOMER_NAME", "No company name found."))
    flags += _recipient_flags(rfq.customer_email, policy, facts)
    for note in rfq.missing_info:
        flags.append(_review("OPEN_QUESTION", note))
    for name in facts.unread_attachments if facts else []:
        flags.append(_review("UNREAD_ATTACHMENT", f"Attachment {name!r} could not be read."))

    # --- each line -----------------------------------------------------------
    for n, match in enumerate(matches, start=1):
        label = f"Line {n} ({match.item.description[:50]})"
        quantity = match.item.quantity
        if quantity is None or quantity <= 0:
            flags.append(_review("MISSING_QUANTITY", f"{label}: no quantity stated."))
        elif match.item.unit is None:
            flags.append(_review("UNKNOWN_UNIT", f"{label}: quantity {quantity:g} has no unit."))
        if match.sku is None:
            flags.append(_review("UNMATCHED_ITEM", f"{label}: {match.reason}"))
            continue
        sku = catalog.get(match.sku)
        if sku is None:
            flags.append(_review("UNMATCHED_ITEM", f"{label}: SKU {match.sku} not in catalog."))
            continue
        if match.confidence != "high":
            flags.append(
                _review(
                    "LOW_MATCH_CONFIDENCE",
                    f"{label}: matched {match.sku} with {match.confidence} confidence. "
                    f"{match.reason}",
                )
            )
        requested, offered = sizes(match.item.description), sizes(sku.description)
        if requested and offered and not requested & offered:
            flags.append(
                _review(
                    "SIZE_MISMATCH",
                    f"{label}: the request states {', '.join(sorted(requested))} but "
                    f"{sku.sku} is {', '.join(sorted(offered))}.",
                )
            )
        if match.item.unit and match.item.unit != sku.unit:
            flags.append(
                _review(
                    "UNIT_MISMATCH",
                    f"{label}: requested in '{match.item.unit}' but {sku.sku} sells per "
                    f"'{sku.unit}'.",
                )
            )
        if quantity is not None and 0 < quantity < sku.moq:
            flags.append(
                _review(
                    "BELOW_MOQ",
                    f"{label}: {quantity:g} is below the minimum order of {sku.moq:g} {sku.unit}.",
                )
            )

    # --- money ---------------------------------------------------------------
    for line in quote.lines:
        if line.margin_pct < policy.margin_floor_pct:
            flags.append(
                _review(
                    "MARGIN_BELOW_FLOOR",
                    f"{line.sku}: margin {line.margin_pct:.1f}% after {line.discount_pct:g}% "
                    f"volume discount is under the {policy.margin_floor_pct:g}% floor.",
                )
            )
    if quote.total >= policy.high_value_eur:
        flags.append(
            _review(
                "HIGH_ORDER_VALUE",
                f"Total EUR {quote.total:,.2f} is at or above the EUR "
                f"{policy.high_value_eur:,.0f} sign-off limit.",
            )
        )

    # --- dates ---------------------------------------------------------------
    requested = _parse_date(rfq.delivery_date)
    if rfq.delivery_date and requested is None:
        flags.append(_review("INVALID_DELIVERY_DATE", f"Cannot read date {rfq.delivery_date!r}."))
    elif requested is None:
        flags.append(
            Flag(
                code="NO_DELIVERY_DATE",
                severity="info",
                detail="No delivery date requested; quote states standard lead times.",
            )
        )
    elif quote.lines:
        slowest = max(quote.lines, key=lambda line: line.lead_time_days)
        earliest = as_of + timedelta(days=slowest.lead_time_days)
        if requested < earliest:
            flags.append(
                _review(
                    "LEAD_TIME_RISK",
                    f"Requested {requested.isoformat()} but {slowest.sku} needs "
                    f"{slowest.lead_time_days} days (earliest {earliest.isoformat()}).",
                )
            )

    if policy.assisted and not any(f.severity == "review" for f in flags):
        flags.append(_review("ASSISTED_MODE", "Auto-approval is switched off; please confirm."))
    return flags


def _recipient_flags(address: str | None, policy: Policy, facts: SourceFacts | None) -> list[Flag]:
    """The quote's recipient is the one field that can send our prices to the wrong party."""
    if not address:
        return [_review("MISSING_CUSTOMER_EMAIL", "No address to send the quote to.")]
    address = address.lower()
    if _domain(address) in policy.own_domains:
        return [_review("INTERNAL_RECIPIENT", f"{address} is one of our own addresses.")]
    if facts is None:
        return []
    if address not in facts.emails_found:
        return [
            _review("EMAIL_NOT_IN_SOURCE", f"{address} does not appear in the source document.")
        ]
    sender = facts.sender_email
    if sender and _domain(sender) not in policy.own_domains and _domain(sender) != _domain(address):
        return [
            _review(
                "RECIPIENT_NOT_SENDER",
                f"Quote would go to {address}, but the request came from {sender}.",
            )
        ]
    return []


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def route_for(flags: list[Flag]) -> Route:
    return "human_review" if any(f.severity == "review" for f in flags) else "auto_approve"
