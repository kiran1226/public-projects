"""Read-only tools. Arguments and results use case-scoped tokens, never real identifiers.

A model can only look up parties the case has already surfaced: every argument must be
a token the gateway issued in this case.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from . import features
from .bankdb import BankDB
from .models import NOT_APPLICABLE, REASON_CODES, TYPOLOGIES, Alert
from .policy import Policy
from .privacy import Gateway, UnknownToken, Vault


@dataclass
class CaseContext:
    db: BankDB
    alert: Alert
    vault: Vault
    gateway: Gateway
    policy: Policy
    sensitive_log: list[dict] = field(default_factory=list)


class ToolError(Exception):
    """A problem the model can fix; the message is returned to it as an error result."""


HANDLERS: dict[str, Callable] = {}
DIRECTIONS, CHANNELS = ("in", "out", "any"), ("cash", "transfer", "card", "any")


def dumps(value: object) -> str:
    """JSON for a model. Non-ASCII stays readable so the outbound check can see it."""
    return json.dumps(value, ensure_ascii=False)


def tool(fn: Callable) -> Callable:
    HANDLERS[fn.__name__] = fn
    return fn


def window(ctx: CaseContext, start: str, end: str) -> tuple[date, date]:
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError) as exc:
        raise ToolError("Dates must be YYYY-MM-DD.") from exc
    last = min(last, ctx.alert.as_of)  # nothing from the alert's future
    if first > last:
        raise ToolError("start is after end (or after the alert's as-of date).")
    return first, last


@tool
def get_customer_profile(ctx: CaseContext, role: str, customer: str) -> dict:
    cid = ctx.vault.lookup(customer, "CUST")
    view = ctx.gateway.profile_view(ctx.db.customer(cid), role)
    view["accounts"] = [ctx.gateway.tok("ACCT", a["account_id"]) for a in ctx.db.accounts(cid)]
    return view


@tool
def get_activity_summary(ctx: CaseContext, role: str, account: str, start: str, end: str) -> dict:
    aid = ctx.vault.lookup(account, "ACCT")
    first, last = window(ctx, start, end)
    owner = ctx.db.customer(ctx.db.account(aid)["customer_id"])
    summary = features.activity_summary(
        ctx.db, aid, first, last, owner["expected_monthly_turnover"], ctx.policy
    )
    summary["top_counterparties"] = [
        {"counterparty": ctx.gateway.tok("CPTY", row.pop("counterparty_id")), **row}
        for row in summary["top_counterparties"]
    ]
    summary["reporting_threshold"] = ctx.policy.reporting_threshold
    summary["expected_monthly_turnover"] = owner["expected_monthly_turnover"]
    return summary


@tool
def get_prior_alerts(ctx: CaseContext, role: str, customer: str) -> dict:
    cid = ctx.vault.lookup(customer, "CUST")
    priors = ctx.db.prior_alerts(cid, before=ctx.alert.as_of)
    return {"prior_alerts": [ctx.gateway.prior_view(p) for p in priors]}


@tool
def list_transactions(
    ctx: CaseContext,
    role: str,
    account: str,
    start: str,
    end: str,
    direction: str,
    channel: str,
    min_amount: float,
    limit: int,
) -> dict:
    aid = ctx.vault.lookup(account, "ACCT")
    first, last = window(ctx, start, end)
    if direction not in DIRECTIONS or channel not in CHANNELS:
        # An unknown filter would return nothing, which reads as "no transactions".
        raise ToolError(f"direction must be one of {DIRECTIONS}, channel one of {CHANNELS}.")
    try:
        floor, cap = float(min_amount), max(1, min(int(limit), ctx.policy.max_rows))
    except (TypeError, ValueError) as exc:
        raise ToolError("min_amount and limit must be numbers.") from exc
    rows = ctx.db.transactions(
        aid,
        first,
        last,
        None if direction == "any" else direction,
        None if channel == "any" else channel,
        floor,
    )
    return {
        "total_matching": len(rows),
        "truncated": len(rows) > cap,
        "rows": [ctx.gateway.txn_view(r) for r in rows[:cap]],
    }


@tool
def get_counterparty(ctx: CaseContext, role: str, counterparty: str) -> dict:
    cp = ctx.db.counterparty(ctx.vault.lookup(counterparty, "CPTY"))
    view = ctx.gateway.counterparty_view(cp)
    view["country_risk"] = "high" if cp["country"] in ctx.policy.high_risk_countries else "standard"
    return view


@tool
def get_related_parties(ctx: CaseContext, role: str, customer: str) -> dict:
    cid = ctx.vault.lookup(customer, "CUST")
    return {
        "related": [
            {
                "customer": ctx.gateway.tok("CUST", link["customer_id"]),
                "link_type": link["link_type"],
            }
            for link in ctx.db.links(cid)
        ]
    }


def _party(ctx: CaseContext, party: str) -> dict:
    key = ctx.vault.lookup(party, "CUST", "CPTY")
    return ctx.db.customer(key) if ctx.vault.kind(party) == "CUST" else ctx.db.counterparty(key)


@tool
def screen_party(ctx: CaseContext, role: str, party: str) -> dict:
    row = _party(ctx, party)  # screening runs on the real name, inside the boundary
    hits = features.screen(
        ctx.db, row["name"], row["dob"], row["country"], ctx.policy.screening_similarity
    )
    return {
        "candidates": [{"entry": ctx.gateway.tok("WATCH", h.pop("entry_id")), **h} for h in hits]
    }


@tool
def search_adverse_media(ctx: CaseContext, role: str, party: str) -> dict:
    row = _party(ctx, party)
    items = [m for m in ctx.db.media(row["name"]) if m["published"] <= ctx.alert.as_of.isoformat()]
    return {
        "items": [
            {
                "id": ctx.gateway.tok("MEDIA", m["media_id"]),
                "published": m["published"],
                "headline": ctx.gateway.scrub(m["headline"]),
                "snippet": ctx.gateway.scrub(m["snippet"]),
            }
            for m in items
        ]
    }


@tool
def request_sensitive_detail(
    ctx: CaseContext, role: str, counterparty: str, justification: str
) -> dict:
    cp = ctx.db.counterparty(ctx.vault.lookup(counterparty, "CPTY"))
    granted = len(str(justification).strip()) >= 20
    ctx.sensitive_log.append(
        {
            "counterparty": counterparty,
            "granted": granted,
            "role": role,
            "justification": str(justification),
        }
    )
    if not granted:
        raise ToolError("Give a specific justification: what in this case depends on the category?")
    view = ctx.gateway.counterparty_view(cp, reveal=True)
    return {
        "counterparty": counterparty,
        "category": view["sensitive"],
        "business_type": view["business_type"],
    }


TOOLSETS: dict[str, tuple[str, ...]] = {
    "triage": (
        "get_customer_profile",
        "get_activity_summary",
        "get_prior_alerts",
        "list_transactions",
    ),
    "lead": (
        "get_customer_profile",
        "get_activity_summary",
        "get_prior_alerts",
        "list_transactions",
        "get_counterparty",
        "get_related_parties",
        "screen_party",
        "search_adverse_media",
        "request_sensitive_detail",
    ),
}
SUBMIT = {"triage": "submit_triage", "lead": "submit_disposition"}


def execute(ctx: CaseContext, role: str, name: str, tool_input: dict) -> tuple[str, bool]:
    """Run one read tool. Returns (JSON text, False) or (error text, True). Never raises."""
    if name not in TOOLSETS.get(role, ()):
        return f"Tool {name!r} is not available to the {role} role.", True
    handler = HANDLERS[name]
    try:
        inspect.signature(handler).bind(ctx, role, **tool_input)
    except TypeError as exc:
        return f"Invalid arguments for {name}: {exc}", True
    try:
        return dumps(handler(ctx, role, **tool_input)), False
    except (ToolError, UnknownToken) as exc:
        return str(exc), True
    except (
        Exception
    ) as exc:  # a tool bug must not take the case down; the model can route around it
        return f"Tool {name} failed: {type(exc).__name__}.", True


# --- schemas ------------------------------------------------------------------------

STR = {"type": "string"}
TOKEN_HELP = "Use a token exactly as it appeared in the brief or a tool result."
FINDINGS = {
    "type": "array",
    "description": "Facts you rely on. Each must cite at least one token as evidence.",
    "items": {
        "type": "object",
        "properties": {
            "statement": STR,
            "evidence_ids": {
                "type": "array",
                "items": STR,
                "description": (
                    "Tokens copied exactly, for example TXN_0007, ACCT_01 or CUST_01. A figure "
                    "from a summary cites the account token. Never a tool name or free text."
                ),
            },
        },
        "required": ["statement", "evidence_ids"],
        "additionalProperties": False,
    },
}
CONFIDENCE = {"type": "string", "enum": ["high", "medium", "low"]}
REASON = {"type": "string", "enum": [*REASON_CODES, NOT_APPLICABLE]}


def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


def _token(kind: str) -> dict:
    return {"type": "string", "description": f"A {kind} token. {TOKEN_HELP}"}


DATE = {"type": "string", "description": "YYYY-MM-DD"}
SCHEMAS = {
    "get_customer_profile": _tool(
        "get_customer_profile",
        "KYC profile of a customer: type, age band, occupation, risk rating, expected monthly "
        "turnover, KYC notes and account tokens.",
        {"customer": _token("CUST")},
    ),
    "get_activity_summary": _tool(
        "get_activity_summary",
        "Code-computed aggregates for an account in a date window: totals by month, cash share, "
        "count of cash deposits just under the reporting threshold, share of incoming money "
        "that left within 48 hours, turnover against the declared profile, top counterparties.",
        {"account": _token("ACCT"), "start": DATE, "end": DATE},
    ),
    "get_prior_alerts": _tool(
        "get_prior_alerts",
        "Earlier alerts on a customer and how they were resolved.",
        {"customer": _token("CUST")},
    ),
    "list_transactions": _tool(
        "list_transactions",
        "Raw transactions of an account in a date window, oldest first. At most 40 rows are "
        "returned; narrow the filters if the result is truncated.",
        {
            "account": _token("ACCT"),
            "start": DATE,
            "end": DATE,
            "direction": {"type": "string", "enum": ["in", "out", "any"]},
            "channel": {"type": "string", "enum": ["cash", "transfer", "card", "any"]},
            "min_amount": {"type": "number", "description": "0 for no minimum"},
            "limit": {"type": "integer", "description": "1 to 40"},
        },
    ),
    "get_counterparty": _tool(
        "get_counterparty",
        "What the bank knows about a counterparty: type, country and its risk, when first "
        "seen, whether it is also a customer.",
        {"counterparty": _token("CPTY")},
    ),
    "get_related_parties": _tool(
        "get_related_parties",
        "Customers linked to this customer by a shared director, address or frequent transfers.",
        {"customer": _token("CUST")},
    ),
    "screen_party": _tool(
        "screen_party",
        "Screen a customer or counterparty against sanctions and PEP lists. Returns candidates "
        "as comparison facts: name similarity, date-of-birth match, country match.",
        {"party": _token("CUST or CPTY")},
    ),
    "search_adverse_media": _tool(
        "search_adverse_media",
        "Negative news about a customer or counterparty.",
        {"party": _token("CUST or CPTY")},
    ),
    "request_sensitive_detail": _tool(
        "request_sensitive_detail",
        "Reveal the category of a withheld counterparty (health, religion, political, trade "
        "union). Use only when the decision depends on it. Every request is logged and shown "
        "to the analyst.",
        {
            "counterparty": _token("CPTY"),
            "justification": {"type": "string", "description": "What in this case depends on it."},
        },
    ),
}
DELEGATE = _tool(
    "delegate",
    "Hand bulk reading to a worker and get back a short digest with evidence tokens. "
    "transaction_digest: target is an ACCT token, start and end bound the window. "
    "screening_check: target is a CUST or CPTY token. prior_alert_summary: target is a CUST "
    "token. For the last two, pass empty strings for start and end.",
    {
        "worker": {
            "type": "string",
            "enum": ["transaction_digest", "screening_check", "prior_alert_summary"],
        },
        "target": STR,
        "start": STR,
        "end": STR,
        "question": {"type": "string", "description": "What the worker should look for."},
    },
)
SUBMIT_TRIAGE = _tool(
    "submit_triage",
    "Finish triage. Call exactly once.",
    {
        "action": {"type": "string", "enum": ["close", "escalate"]},
        "reason_code": REASON,
        "confidence": CONFIDENCE,
        "findings": FINDINGS,
        "handoff_notes": {
            "type": "string",
            "description": "For an escalation: what you checked and what is unresolved.",
        },
    },
)
SUBMIT_DISPOSITION = _tool(
    "submit_disposition",
    "Finish the investigation. Call exactly once.",
    {
        "outcome": {
            "type": "string",
            "enum": ["close_false_positive", "escalate_l2", "needs_info"],
        },
        "reason_code": REASON,
        "typology": {"type": "string", "enum": [*TYPOLOGIES, NOT_APPLICABLE]},
        "confidence": CONFIDENCE,
        "findings": FINDINGS,
        "narrative": {
            "type": "string",
            "description": "For an escalation: who, what, when, where, why.",
        },
        "open_questions": {"type": "array", "items": STR},
    },
)


def schemas_for(role: str) -> list[dict]:
    schemas = [SCHEMAS[name] for name in TOOLSETS[role]]
    if role == "lead":
        return [*schemas, DELEGATE, SUBMIT_DISPOSITION]
    return [*schemas, SUBMIT_TRIAGE]
