import pytest
from pydantic import ValidationError

from amlagent.models import parse_submission, parse_triage

FINDING = [{"statement": "Salary from employer", "evidence_ids": ["TXN_0001"]}]


def submission(**over):
    base = {
        "outcome": "close_false_positive",
        "reason_code": "explained_one_off",
        "typology": "not_applicable",
        "confidence": "high",
        "findings": FINDING,
        "narrative": "",
        "open_questions": [],
    }
    return base | over


def test_not_applicable_becomes_none():
    d = parse_submission(submission())
    assert d.typology is None and d.reason_code == "explained_one_off"


def test_close_without_reason_code_is_rejected():
    with pytest.raises(ValidationError):
        parse_submission(submission(reason_code="not_applicable"))


def test_escalation_needs_typology_and_narrative():
    with pytest.raises(ValidationError):
        parse_submission(
            submission(
                outcome="escalate_l2",
                reason_code="not_applicable",
                typology="structuring",
                narrative="  ",
            )
        )
    d = parse_submission(
        submission(
            outcome="escalate_l2",
            reason_code="not_applicable",
            typology="structuring",
            narrative="CUST_01 deposited cash.",
        )
    )
    assert d.typology == "structuring"


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        parse_submission(submission(surprise=1))


def test_parse_triage_close_and_escalate():
    close = {
        "action": "close",
        "reason_code": "explained_one_off",
        "confidence": "high",
        "findings": FINDING,
        "handoff_notes": "",
    }
    d, notes = parse_triage(close)
    assert d.outcome == "close_false_positive" and notes == ""
    d, notes = parse_triage(
        close
        | {
            "action": "escalate",
            "reason_code": "not_applicable",
            "findings": [],
            "handoff_notes": "Cash pattern unexplained.",
        }
    )
    assert d is None and notes == "Cash pattern unexplained."


def test_a_submission_with_missing_fields_is_a_value_error():
    for broken in (
        {"action": "close"},
        {"action": "close", "reason_code": "explained_one_off"},
        {},
    ):
        with pytest.raises(ValueError):
            parse_triage(broken)
    with pytest.raises(ValueError):
        parse_submission({"outcome": "close_false_positive"})
