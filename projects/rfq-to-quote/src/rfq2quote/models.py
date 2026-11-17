"""Typed data contracts shared by every pipeline step."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Unit = Literal["m", "pcs"]
Confidence = Literal["high", "medium", "low"]
Route = Literal["auto_approve", "human_review"]


class Strict(BaseModel):
    """Base model: unknown fields are an error, so schema drift fails loudly."""

    model_config = ConfigDict(extra="forbid")


# --- extraction contract (the schema the LLM must fill) -----------------------
# Field descriptions say what a field is. How to read messy input is the prompt's
# job, so that prompt versions can be compared against the same schema.


class LineItem(Strict):
    description: str = Field(description="The product requested, with type, size and rating.")
    quantity: float | None = Field(description="Requested quantity, or null if not stated.")
    unit: Unit | None = Field(
        description="'m' for goods bought by length, 'pcs' for countable goods, null if unclear."
    )


class RFQ(Strict):
    customer_name: str | None = Field(description="Company that is asking for the quote.")
    customer_email: str | None = Field(description="Email address the quote should go to.")
    delivery_date: str | None = Field(description="Requested delivery date as YYYY-MM-DD.")
    delivery_location: str | None = Field(description="City or site the goods should go to.")
    line_items: list[LineItem]
    missing_info: list[str] = Field(
        description="Open points a sales rep must resolve before this can be quoted correctly."
    )


# --- matching contract --------------------------------------------------------


class MatchDecision(Strict):
    """What the LLM returns when asked to pick a SKU from retrieved candidates."""

    sku: str | None = Field(description="SKU id from the candidate list, or null if none fits.")
    confidence: Confidence
    reason: str = Field(description="One sentence on why this SKU fits or why none does.")


class MatchedLine(Strict):
    item: LineItem
    sku: str | None
    confidence: Confidence
    reason: str
    candidates: list[str] = Field(default_factory=list)


# --- quote, guardrails, result -----------------------------------------------


class QuoteLine(Strict):
    sku: str
    description: str
    quantity: float
    unit: Unit
    list_price: float
    discount_pct: float
    unit_price: float
    line_total: float
    margin_pct: float
    lead_time_days: int


class Quote(Strict):
    currency: str = "EUR"
    lines: list[QuoteLine] = Field(default_factory=list)
    total: float = 0.0


class Flag(Strict):
    code: str
    severity: Literal["review", "info"]
    detail: str


class Usage(Strict):
    """LLM spend for one RFQ. Mutated as calls are made."""

    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0  # summed over calls; calls for one request may overlap

    def add(self, other: Usage) -> None:
        for name in type(self).model_fields:
            setattr(self, name, getattr(self, name) + getattr(other, name))


class SourceFacts(Strict):
    """Facts about the source document that guardrails check the extraction against."""

    sender_email: str | None = None
    emails_found: list[str] = Field(default_factory=list)
    unread_attachments: list[str] = Field(default_factory=list)


class Provenance(Strict):
    """What produced this result, so any quote can be traced and reproduced."""

    doc_sha256: str = ""
    extractor: str = ""
    matcher: str = ""
    model: str | None = None
    prompt_sha256: str | None = None
    catalog_sha256: str = ""
    policy: dict = Field(default_factory=dict)


class PipelineResult(Strict):
    rfq_id: str
    source: str
    as_of: str
    rfq: RFQ
    matches: list[MatchedLine]
    quote: Quote
    flags: list[Flag]
    route: Route
    usage: Usage
    facts: SourceFacts = Field(default_factory=SourceFacts)
    provenance: Provenance = Field(default_factory=Provenance)
