"""The gate between the model and the list: only what the notice itself supports gets through.

The model returns a judgement and the passages it rests on. The gate checks each
passage against the notice text the model was shown.

- A condition whose passage is not in the notice is dropped and counted.
- A decision whose passage is not in the notice becomes "ask a person". This matters
  most for "skip": a tender is never taken off the top of a list on the strength of a
  quotation that does not exist.
- The deadline and the value are never taken from the model. They come from the
  notice's structured fields.
"""

from __future__ import annotations

import re
import unicodedata

from .models import Assessment, GatedAssessment

# A quotation shorter than this proves nothing: any two letters occur in any notice.
MIN_QUOTE_CHARS = 12
MAX_CONDITIONS = 5
_WS = re.compile(r"\s+")
_EDGE = " \t\n\"'“”„‚‘’«».,;:…-–"


def _canonical(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFKC", text)).strip().casefold()


def quote_in_source(quote: str | None, source: str) -> bool:
    """True if the quotation occurs in the source, ignoring spacing, case and outer punctuation."""
    if not quote:
        return False
    cleaned = _canonical(quote.strip(_EDGE))
    if len(cleaned) < MIN_QUOTE_CHARS:
        return False
    return cleaned in _canonical(source)


def gate(assessment: Assessment, source: str, cost_usd: float = 0.0) -> GatedAssessment:
    """Check an assessment against the notice text the model was shown."""
    kept = [c for c in assessment.conditions if quote_in_source(c.quote, source)]
    dropped = len(assessment.conditions) - len(kept)
    reason_supported = quote_in_source(assessment.reason_quote, source)
    decision = assessment.decision if reason_supported else "ask_person"
    return GatedAssessment(
        fit=max(0, min(100, assessment.fit)),
        decision=decision,
        reason=assessment.reason.strip(),
        reason_quote=assessment.reason_quote.strip() if reason_supported else None,
        conditions=kept[:MAX_CONDITIONS],
        dropped_conditions=dropped + max(0, len(kept) - MAX_CONDITIONS),
        downgraded=decision != assessment.decision,
        cost_usd=round(cost_usd, 6),
    )
