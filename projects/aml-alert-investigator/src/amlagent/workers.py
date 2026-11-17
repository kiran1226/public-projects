"""Workers: code fetches the data, one small-model call turns it into a typed digest.

The lead reads digests, not raw rows, which keeps the expensive context small.
"""

from __future__ import annotations

import json

from .llm import (
    BudgetExceeded,
    CassetteMiss,
    LeakBlocked,
    LLMError,
    LLMProvider,
    StepRequest,
    cost_usd,
)
from .models import Step
from .privacy import UnknownToken
from .roles import DATA_PROTECTION, ModelConfig
from .tools import CaseContext, ToolError, dumps, screen_party, window

STR = {"type": "string"}
IDS = {"type": "array", "items": STR}


def _schema(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


HEAD = f"You support a financial-crime investigator at a bank. {DATA_PROTECTION}\n\n"
WORKERS = {
    "transaction_digest": {
        "system": HEAD
        + "You receive a question and the raw transactions of one account in a date window. Describe the patterns that bear on the question: timing, amounts, channels, counterparties, and what stays unexplained. For each pattern cite the transaction tokens that show it. Report what the data shows; do not decide the case.",
        "schema": _schema(
            {
                "summary": STR,
                "patterns": {
                    "type": "array",
                    "items": _schema({"description": STR, "evidence_ids": IDS}),
                },
            }
        ),
    },
    "screening_check": {
        "system": HEAD
        + "You receive a party and watchlist candidates as comparison facts: name similarity, date-of-birth match, country match, list type. Decide whether the party is the listed entry. A matching name with a different date of birth or country points to a different party; matching name, date of birth and country point to a true match. Say unclear when the facts do not settle it.",
        "schema": _schema(
            {
                "verdict": {"type": "string", "enum": ["match", "no_match", "unclear"]},
                "reasons": IDS,
                "evidence_ids": IDS,
            }
        ),
    },
    "prior_alert_summary": {
        "system": HEAD
        + "You receive a question and the earlier alerts on a customer with their outcomes and analyst notes. Summarise what was concluded before and name the earlier alerts that bear on the question.",
        "schema": _schema({"summary": STR, "relevant_prior_ids": IDS}),
    },
}


def _payload(
    ctx: CaseContext, worker: str, target: str, start: str, end: str, question: str
) -> dict:
    if worker == "transaction_digest":
        account = ctx.vault.lookup(target, "ACCT")
        first, last = window(ctx, start, end)
        rows = ctx.db.transactions(account, first, last)[: ctx.policy.max_worker_rows]
        return {
            "question": question,
            "account": target,
            "window": [first.isoformat(), last.isoformat()],
            "transactions": [ctx.gateway.txn_view(r) for r in rows],
        }
    if worker == "screening_check":
        key = ctx.vault.lookup(target, "CUST", "CPTY")
        if ctx.vault.kind(target) == "CUST":
            subject = ctx.gateway.profile_view(ctx.db.customer(key), "lead")
        else:
            subject = ctx.gateway.counterparty_view(ctx.db.counterparty(key))
        return {
            "question": question,
            "subject": subject,
            "candidates": screen_party(ctx, "lead", target)["candidates"],
        }
    customer = ctx.vault.lookup(target, "CUST")
    priors = ctx.db.prior_alerts(customer, before=ctx.alert.as_of)
    return {"question": question, "prior_alerts": [ctx.gateway.prior_view(p) for p in priors]}


def _parse(text: str, schema: dict) -> dict:
    """Read a JSON object from the reply, tolerating code fences and stray prose."""
    begin, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[begin : end + 1])
    except ValueError as exc:
        raise ToolError("The worker did not return valid JSON.") from exc
    missing = [k for k in schema["required"] if k not in data]
    if not isinstance(data, dict) or missing:
        raise ToolError(f"The worker reply is missing fields: {missing}.")
    return data


class WorkerReplyError(ToolError):
    """The worker was called and paid for, but its reply is unusable. Carries the step,
    so the case's cost and audit trail still show the call."""

    def __init__(self, message: str, step: Step):
        super().__init__(message)
        self.step = step


def run_worker(
    provider: LLMProvider,
    cfg: ModelConfig,
    ctx: CaseContext,
    worker: str,
    target: str,
    start: str,
    end: str,
    question: str,
    *,
    tag: str,
) -> tuple[dict, Step]:
    if worker not in WORKERS:
        raise ToolError(f"Unknown worker {worker!r}.")
    spec = WORKERS[worker]
    try:
        payload = _payload(ctx, worker, target, start, end, question)
    except UnknownToken as exc:
        raise ToolError(str(exc)) from exc
    request = StepRequest(
        model=cfg.worker_model,
        effort=cfg.worker_effort,
        system=spec["system"],
        messages=[{"role": "user", "content": dumps(payload)}],
        output_schema=spec["schema"],
        max_tokens=2000,
        tag=f"{tag}/worker:{worker}",
    )
    try:
        response = provider.step(request)
    except (CassetteMiss, BudgetExceeded, LeakBlocked):
        raise
    except LLMError as exc:
        raise ToolError(f"The {worker} worker failed: {exc}") from exc
    text = next((b["text"] for b in response.content if b.get("type") == "text"), "")
    step = Step(
        role=f"worker:{worker}",
        model=response.model,
        stop_reason=response.stop_reason,
        cost_usd=cost_usd(response.model, response.usage),
        latency_s=response.latency_s,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cache_read_tokens=response.usage.cache_read_tokens,
        cache_write_tokens=response.usage.cache_write_tokens,
    )
    try:
        return _parse(text, spec["schema"]), step
    except ToolError as exc:
        raise WorkerReplyError(str(exc), step) from exc
