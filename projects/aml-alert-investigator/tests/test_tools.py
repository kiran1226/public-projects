import json
from datetime import date

import pytest

from amlagent.policy import Policy
from amlagent.privacy import Gateway, Vault
from amlagent.tools import SUBMIT, TOOLSETS, CaseContext, execute, schemas_for

LIST = {
    "start": "2026-06-01",
    "end": "2026-06-03",
    "direction": "any",
    "channel": "any",
    "min_amount": 0,
    "limit": 10,
}


@pytest.fixture
def ctx(db, alert_flagged):
    vault = Vault(alert_flagged.alert_id)
    return CaseContext(db, alert_flagged, vault, Gateway(db, vault, alert_flagged.as_of), Policy())


def call(ctx, role, name, **tool_input):
    text, is_error = execute(ctx, role, name, tool_input)
    assert not is_error, text
    return json.loads(text)


def test_profile_and_transactions_use_tokens_only(ctx):
    profile = call(ctx, "triage", "get_customer_profile", customer=ctx.gateway.tok("CUST", "C0001"))
    rows = call(
        ctx,
        "triage",
        "list_transactions",
        account=profile["accounts"][0],
        **LIST | {"direction": "in", "channel": "cash"},
    )
    assert rows["total_matching"] == 3 and not rows["truncated"]
    assert all(r["id"].startswith("TXN_") for r in rows["rows"])
    dumped = json.dumps([profile, rows])
    assert not any(real in dumped for real in ("T000001", "A0001", "C0001", "Anna", "DE44HARB"))


def test_window_never_reaches_past_the_as_of_date(ctx):
    ctx.alert = ctx.alert.model_copy(update={"as_of": date(2026, 6, 5)})
    acct = ctx.gateway.tok("ACCT", "A0001")
    rows = call(ctx, "triage", "list_transactions", account=acct, **LIST | {"end": "2026-12-31"})
    assert rows["total_matching"] == 4  # T000005 onwards is in the future of this alert


def test_limit_is_capped_and_reported(ctx):
    acct = ctx.gateway.tok("ACCT", "A0001")
    rows = call(ctx, "triage", "list_transactions", account=acct, **LIST | {"limit": 2})
    assert len(rows["rows"]) == 2 and rows["truncated"] and rows["total_matching"] == 4


@pytest.mark.parametrize(
    "tool_input",
    [
        {"customer": "CUST_99"},  # never issued
        {"customer": "ACCT_01"},  # wrong kind (issued below)
        {},  # missing argument
        {"customer": "CUST_01", "extra": 1},  # unexpected argument
    ],
)
def test_bad_arguments_come_back_as_errors(ctx, tool_input):
    ctx.gateway.tok("ACCT", "A0001")
    ctx.gateway.tok("CUST", "C0001")
    text, is_error = execute(ctx, "triage", "get_customer_profile", tool_input)
    assert is_error and text


def test_bad_dates_come_back_as_errors(ctx):
    acct = ctx.gateway.tok("ACCT", "A0001")
    for bad in (
        {"start": "June 1"},
        {"start": "2026-06-10", "end": "2026-06-01"},
        {"min_amount": "lots"},
    ):
        text, is_error = execute(ctx, "triage", "list_transactions", {"account": acct} | LIST | bad)
        assert is_error, bad


def test_a_crashing_tool_becomes_an_error_result(ctx, monkeypatch):
    from amlagent import tools

    def broken(ctx, role, customer):
        raise RuntimeError("database gone")

    monkeypatch.setitem(tools.HANDLERS, "get_customer_profile", broken)
    text, is_error = execute(ctx, "triage", "get_customer_profile", {"customer": "CUST_01"})
    assert is_error and "RuntimeError" in text and "database gone" not in text


def test_roles_get_different_tools(ctx):
    cust = ctx.gateway.tok("CUST", "C0001")
    text, is_error = execute(ctx, "triage", "screen_party", {"party": cust})
    assert is_error and "not available" in text
    assert "delegate" not in [s["name"] for s in schemas_for("triage")]
    assert {"delegate", "request_sensitive_detail", SUBMIT["lead"]} <= {
        s["name"] for s in schemas_for("lead")
    }
    assert set(TOOLSETS["triage"]) < set(TOOLSETS["lead"])


def test_every_schema_is_strict():
    for role in ("triage", "lead"):
        for schema in schemas_for(role):
            body = schema["input_schema"]
            assert schema["strict"] is True and body["additionalProperties"] is False
            assert body["required"] == list(body["properties"]), schema["name"]


def test_activity_summary_tokenises_counterparties(ctx):
    acct = ctx.gateway.tok("ACCT", "A0001")
    summary = call(
        ctx, "triage", "get_activity_summary", account=acct, start="2026-06-01", end="2026-06-03"
    )
    assert summary["structuring"]["count"] == 3 and summary["reporting_threshold"] == 10000.0
    assert summary["top_counterparties"][0]["counterparty"].startswith("CPTY_")
    assert "counterparty_id" not in json.dumps(summary)


def test_screening_returns_comparison_facts_not_names(ctx):
    out = call(ctx, "lead", "screen_party", party=ctx.gateway.tok("CPTY", "P0001"))
    assert out["candidates"] == [
        {
            "entry": "WATCH_01",
            "list": "sanctions",
            "kind": "person",
            "name_similarity": 1.0,
            "dob_match": "match",
            "country_match": "match",
        }
    ]
    assert "Wolf" not in json.dumps(out)


def test_sensitive_detail_needs_a_reason_and_is_logged(ctx):
    cp = ctx.gateway.tok("CPTY", "P0002")
    text, is_error = execute(
        ctx, "lead", "request_sensitive_detail", {"counterparty": cp, "justification": "why"}
    )
    assert is_error and ctx.sensitive_log[-1]["granted"] is False
    out = call(
        ctx,
        "lead",
        "request_sensitive_detail",
        counterparty=cp,
        justification="Monthly payments of 4,800 are not explained by the profile.",
    )
    assert out["category"] == "health" and "name" not in out
    assert ctx.sensitive_log[-1] == {
        "counterparty": cp,
        "granted": True,
        "role": "lead",
        "justification": "Monthly payments of 4,800 are not explained by the profile.",
    }


def test_media_counterparty_and_related_parties(ctx):
    cp = ctx.gateway.tok("CPTY", "P0003")
    media = call(ctx, "lead", "search_adverse_media", party=cp)
    assert media["items"][0]["headline"] == f"{cp} named in invoice fraud inquiry"
    view = call(ctx, "lead", "get_counterparty", counterparty=cp)
    assert view["country_risk"] == "high" and view["is_customer"] is False
    related = call(ctx, "lead", "get_related_parties", customer=ctx.gateway.tok("CUST", "C0001"))
    assert related["related"] == [
        {"customer": ctx.gateway.tok("CUST", "C0002"), "link_type": "shared_director"}
    ]
    priors = call(ctx, "triage", "get_prior_alerts", customer=ctx.gateway.tok("CUST", "C0001"))
    assert priors["prior_alerts"][0]["outcome"] == "escalated"


def test_non_ascii_text_is_not_escaped_out_of_sight(ctx, monkeypatch):
    from amlagent import tools

    monkeypatch.setitem(
        tools.HANDLERS, "get_customer_profile", lambda ctx, role, customer: {"note": "Jörg Müller"}
    )
    text, _ = execute(ctx, "triage", "get_customer_profile", {"customer": "CUST_01"})
    assert "Jörg Müller" in text  # an escaped \u00f6 would hide the name from the outbound check
    assert tools.dumps({"a": "ö"}) == '{"a": "ö"}'


def test_out_of_range_filters_are_errors_not_empty_results(ctx):
    acct = ctx.gateway.tok("ACCT", "A0001")
    for bad in ({"direction": "inbound"}, {"channel": "wire"}):
        text, is_error = execute(ctx, "triage", "list_transactions", {"account": acct} | LIST | bad)
        assert is_error, bad
