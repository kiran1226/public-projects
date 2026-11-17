from amlagent.models import Disposition, Finding
from amlagent.policy import RULES, Policy, citation_hits, gate, triage_close_hits

ISSUED = {"TXN_0001", "TXN_0002", "CUST_01"}
P = Policy()


def close(**over):
    base = dict(
        outcome="close_false_positive",
        reason_code="explained_one_off",
        confidence="high",
        findings=[Finding(statement="Bonus from employer", evidence_ids=["TXN_0001"])],
    )
    return Disposition(**base | over)


def escalate(**over):
    base = dict(
        outcome="escalate_l2",
        typology="structuring",
        confidence="high",
        narrative="CUST_01 made cash deposits under the threshold.",
        findings=[
            Finding(statement="Deposits under threshold", evidence_ids=["TXN_0001", "TXN_0002"])
        ],
    )
    return Disposition(**base | over)


def run(disposition, **over):
    kwargs = dict(
        tier="lead",
        hard_flags=[],
        shown=ISSUED,
        failure=None,
        sensitive_used=False,
        mode="assisted",
        alert_id="AL-001",
        policy=P,
    )
    queue, hits = gate(disposition, **kwargs | over)
    return queue, [h.code for h in hits]


def test_clean_dispositions_go_to_the_analyst():
    assert run(close()) == ("analyst_review", [])
    assert run(escalate()) == ("analyst_review", [])


def test_a_citation_the_gateway_never_issued_is_rejected():
    bad = close(findings=[Finding(statement="Invented", evidence_ids=["TXN_0001", "TXN_9999"])])
    assert [h.code for h in citation_hits(bad, ISSUED)] == ["CITATION_UNVERIFIED"]
    assert run(bad) == ("manual_investigation", ["CITATION_UNVERIFIED"])


def test_findings_need_evidence():
    assert run(close(findings=[Finding(statement="Trust me", evidence_ids=[])])) == (
        "manual_investigation",
        ["FINDING_UNCITED"],
    )
    assert run(close(findings=[])) == ("manual_investigation", ["FINDING_UNCITED"])
    info = Disposition(
        outcome="needs_info", confidence="medium", findings=[], open_questions=["Source of funds?"]
    )
    assert run(info) == ("analyst_review", [])  # a question needs no evidence


def test_low_confidence_is_not_a_recommendation():
    assert run(close(confidence="low")) == ("manual_investigation", ["LOW_CONFIDENCE"])


def test_close_on_a_flagged_alert_needs_senior_sign_off():
    assert run(close(), hard_flags=["LARGE_AMOUNT"]) == ("senior_review", ["CLOSE_WITH_HARD_FLAG"])
    assert run(escalate(), hard_flags=["LARGE_AMOUNT"]) == ("analyst_review", [])


def test_failures_route_to_manual_investigation():
    assert run(None, failure="limit") == ("manual_investigation", ["LIMIT_EXCEEDED"])
    assert run(None, failure="leak_blocked") == ("manual_investigation", ["LEAK_BLOCKED"])
    assert run(None, failure="model_failure: refusal") == (
        "manual_investigation",
        ["MODEL_FAILURE"],
    )
    assert run(close(), failure="limit") == (
        "manual_investigation",
        ["LIMIT_EXCEEDED"],
    )  # failure wins


def test_sensitive_access_is_flagged_but_does_not_block():
    assert run(close(), sensitive_used=True) == ("analyst_review", ["SENSITIVE_ACCESS_USED"])


def test_triage_close_rule():
    assert triage_close_hits(close(), ISSUED, P) == []
    assert [h.code for h in triage_close_hits(close(confidence="medium"), ISSUED, P)] == [
        "TRIAGE_CONFIDENCE"
    ]
    barred = close(reason_code="screening_false_match")
    assert [h.code for h in triage_close_hits(barred, ISSUED, P)] == ["TRIAGE_REASON_NOT_ALLOWED"]
    uncited = close(findings=[Finding(statement="x", evidence_ids=["TXN_9999"])])
    assert [h.code for h in triage_close_hits(uncited, ISSUED, P)] == ["CITATION_UNVERIFIED"]


def test_auto_close_applies_only_to_clean_triage_closes():
    never_sample = Policy(qa_sample_rate=0.0)
    assert run(close(), tier="triage", mode="auto-close", policy=never_sample) == (
        "auto_closed",
        [],
    )
    assert run(close(), tier="lead", mode="auto-close", policy=never_sample) == (
        "analyst_review",
        [],
    )
    assert run(escalate(), tier="triage", mode="auto-close", policy=never_sample) == (
        "analyst_review",
        [],
    )
    always_sample = Policy(qa_sample_rate=1.0)
    assert run(close(), tier="triage", mode="auto-close", policy=always_sample) == (
        "analyst_review",
        ["QA_SAMPLE"],
    )
    assert run(close(), tier="triage", mode="assisted", policy=never_sample) == (
        "analyst_review",
        [],
    )


def test_every_rule_has_a_sentence():
    assert all(text.endswith(".") for text in RULES.values()) and len(RULES) == 13


def test_an_instruction_in_the_data_overrides_any_recommendation():
    clean = dict(tier="triage", mode="auto-close", policy=Policy(qa_sample_rate=0.0))
    assert run(close(), **clean) == ("auto_closed", [])
    assert run(close(), **clean, instruction_in_data=True) == (
        "manual_investigation",
        ["INSTRUCTION_IN_DATA"],
    )
    assert run(escalate(), instruction_in_data=True)[0] == "manual_investigation"
