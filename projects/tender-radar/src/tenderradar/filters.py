"""Hard rules that take a tender off a profile's list before anything is ranked.

Each rule has a name, and a tender that is removed is removed for a named reason.
A field the buyer left out never removes a tender: missing is not failing.
"""

from __future__ import annotations

from datetime import date

from .config import EUR_PER_UNIT
from .models import Notice, Profile, RuleHit

DEADLINE = "deadline_too_close"
TERRITORY = "outside_territory"
VALUE = "value_out_of_band"
PROCEDURE = "excluded_procedure"
RULES = (DEADLINE, TERRITORY, VALUE, PROCEDURE)


def value_in_eur(notice: Notice) -> float | None:
    """The estimated value in euros, or None if it is absent or in a currency not listed."""
    if notice.estimated_value is None or not notice.currency:
        return None
    rate = EUR_PER_UNIT.get(notice.currency.upper())
    return notice.estimated_value * rate if rate else None


def apply_filters(profile: Profile, notice: Notice, today: date) -> list[RuleHit]:
    """The rules this notice breaks for this profile. An empty list means it stays."""
    hits: list[RuleHit] = []
    if notice.deadline is not None:
        days_left = (notice.deadline - today).days
        if days_left < profile.min_days_to_deadline:
            hits.append(
                RuleHit(
                    rule=DEADLINE,
                    detail=f"{days_left} days to the deadline, "
                    f"{profile.min_days_to_deadline} needed",
                )
            )
    if profile.countries and notice.buyer_country and notice.buyer_country not in profile.countries:
        hits.append(RuleHit(rule=TERRITORY, detail=f"buyer is in {notice.buyer_country}"))
    value = value_in_eur(notice)
    if value is not None:
        if profile.min_value_eur is not None and value < profile.min_value_eur:
            hits.append(RuleHit(rule=VALUE, detail=f"about EUR {value:,.0f}, below the band"))
        elif profile.max_value_eur is not None and value > profile.max_value_eur:
            hits.append(RuleHit(rule=VALUE, detail=f"about EUR {value:,.0f}, above the band"))
    if notice.procedure_type and notice.procedure_type in profile.excluded_procedure_types:
        hits.append(RuleHit(rule=PROCEDURE, detail=f"procedure type {notice.procedure_type}"))
    elif notice.nature and notice.nature in profile.excluded_natures:
        hits.append(RuleHit(rule=PROCEDURE, detail=f"contract for {notice.nature}"))
    return hits
