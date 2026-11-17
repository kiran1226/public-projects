"""Pricing. Plain arithmetic on catalog data: no LLM touches a price."""

from __future__ import annotations

from .catalog import Catalog
from .models import MatchedLine, Quote, QuoteLine

# (minimum line value at list price in EUR, discount)
VOLUME_DISCOUNTS = ((50_000, 0.06), (20_000, 0.04), (5_000, 0.02))


def discount_for(line_value: float) -> float:
    for threshold, discount in VOLUME_DISCOUNTS:
        if line_value >= threshold:
            return discount
    return 0.0


def build_quote(matches: list[MatchedLine], catalog: Catalog) -> Quote:
    """Price every line that has both a SKU and a quantity. Others are left to guardrails."""
    lines = []
    for match in matches:
        sku = catalog.get(match.sku) if match.sku else None
        qty = match.item.quantity
        if sku is None or qty is None or qty <= 0:
            continue
        discount = discount_for(qty * sku.list_price)
        unit_price = round(sku.list_price * (1 - discount), 4)
        lines.append(
            QuoteLine(
                sku=sku.sku,
                description=sku.description,
                quantity=qty,
                unit=sku.unit,
                list_price=sku.list_price,
                discount_pct=round(discount * 100, 2),
                unit_price=unit_price,
                line_total=round(qty * unit_price, 2),
                margin_pct=round((unit_price - sku.unit_cost) / unit_price * 100, 2),
                lead_time_days=sku.lead_time_days,
            )
        )
    return Quote(lines=lines, total=round(sum(line.line_total for line in lines), 2))
