import json

import pytest

from amlagent.llm import LeakBlocked, LLMError, ScriptedProvider
from amlagent.policy import Policy
from amlagent.privacy import Gateway, Vault
from amlagent.roles import CONFIGS, lead_role, triage_role
from amlagent.tools import CaseContext, ToolError
from amlagent.workers import WORKERS, run_worker
from conftest import reply, text


@pytest.fixture
def ctx(db, alert_flagged):
    vault = Vault(alert_flagged.alert_id)
    return CaseContext(db, alert_flagged, vault, Gateway(db, vault, alert_flagged.as_of), Policy())


def test_configs_assign_models_and_effort_per_role():
    routed = CONFIGS["routed"]
    assert (routed.triage_model, routed.lead_model, routed.worker_model) == (
        "claude-haiku-5-5",
        "claude-opus-5-5",
        "claude-haiku-5-5",
    )
    assert (routed.triage_effort, routed.lead_effort, routed.worker_effort) == (
        "low",
        "medium",
        "low",
    )
    assert CONFIGS["haiku"].lead_model == "claude-haiku-5-5"
    nim = CONFIGS["nim"]
    assert "/" in nim.triage_model and nim.triage_model == nim.lead_model == nim.worker_model


def test_roles_carry_the_right_tools_and_limits():
    triage, lead = triage_role(CONFIGS["routed"]), lead_role(CONFIGS["routed"])
    assert (triage.name, triage.submit_tool, triage.max_steps) == ("triage", "submit_triage", 6)
    assert (lead.name, lead.submit_tool, lead.max_steps, lead.max_tokens) == (
        "lead",
        "submit_disposition",
        8,
        6000,
    )
    assert "delegate" in [t["name"] for t in lead.tools] and "delegate" not in [
        t["name"] for t in triage.tools
    ]
    for role in (triage, lead):  # prompts explain tokens and the citation rule
        assert "token" in role.system.lower() and role.submit_tool in role.system


def test_transaction_digest_sends_tokens_and_returns_the_digest(ctx):
    digest = {
        "summary": "Three cash deposits under the threshold.",
        "patterns": [{"description": "structuring", "evidence_ids": ["TXN_0001"]}],
    }
    provider = ScriptedProvider([reply(text(json.dumps(digest)), stop="end_turn")])
    acct = ctx.gateway.tok("ACCT", "A0001")
    result, step = run_worker(
        provider,
        CONFIGS["routed"],
        ctx,
        "transaction_digest",
        acct,
        "2026-06-01",
        "2026-06-03",
        "Any structuring?",
        tag="AL-900/lead",
    )
    assert result == digest and step.role == "worker:transaction_digest"
    sent = provider.requests[0]
    assert sent.tag == "AL-900/lead/worker:transaction_digest" and sent.model == "claude-haiku-5-5"
    assert sent.output_schema == WORKERS["transaction_digest"]["schema"] and sent.tools == []
    payload = json.loads(sent.messages[0]["content"])
    assert len(payload["transactions"]) == 4 and payload["question"] == "Any structuring?"
    assert (
        "T000001" not in sent.messages[0]["content"] and "Jonas" not in sent.messages[0]["content"]
    )


def test_screening_check_gets_comparison_facts(ctx):
    verdict = {"verdict": "match", "reasons": ["Same date of birth."], "evidence_ids": ["WATCH_01"]}
    provider = ScriptedProvider(
        [reply(text(f"```json\n{json.dumps(verdict)}\n```"), stop="end_turn")]
    )
    cp = ctx.gateway.tok("CPTY", "P0001")
    result, _ = run_worker(
        provider, CONFIGS["routed"], ctx, "screening_check", cp, "", "", "Same person?", tag="t"
    )
    assert result == verdict  # code fences are tolerated
    payload = json.loads(provider.requests[0].messages[0]["content"])
    assert (
        payload["candidates"][0]["dob_match"] == "match"
        and payload["subject"]["counterparty"] == cp
    )


def test_prior_alert_summary(ctx):
    out = {"summary": "One earlier escalation.", "relevant_prior_ids": ["PRIOR_01"]}
    provider = ScriptedProvider([reply(text(json.dumps(out)), stop="end_turn")])
    cust = ctx.gateway.tok("CUST", "C0001")
    result, _ = run_worker(
        provider, CONFIGS["routed"], ctx, "prior_alert_summary", cust, "", "", "History?", tag="t"
    )
    assert result == out


@pytest.mark.parametrize(
    "response, target",
    [
        (reply(text("not json"), stop="end_turn"), None),  # unparseable
        (reply(text('{"summary": "x"}'), stop="end_turn"), None),  # missing required key
        (LLMError("503"), None),  # worker call failed
        (reply(text("{}"), stop="end_turn"), "CUST_99"),  # unknown token
    ],
)
def test_worker_problems_are_tool_errors(ctx, response, target):
    acct = target or ctx.gateway.tok("ACCT", "A0001")
    with pytest.raises(ToolError):
        run_worker(
            ScriptedProvider([response]),
            CONFIGS["routed"],
            ctx,
            "transaction_digest",
            acct,
            "2026-06-01",
            "2026-06-03",
            "q",
            tag="t",
        )


def test_unknown_worker_and_leak_block(ctx):
    acct = ctx.gateway.tok("ACCT", "A0001")
    with pytest.raises(ToolError):
        run_worker(ScriptedProvider([]), CONFIGS["routed"], ctx, "nope", acct, "", "", "q", tag="t")
    with pytest.raises(LeakBlocked):
        run_worker(
            ScriptedProvider([LeakBlocked(["iban_pattern"])]),
            CONFIGS["routed"],
            ctx,
            "transaction_digest",
            acct,
            "2026-06-01",
            "2026-06-03",
            "q",
            tag="t",
        )
