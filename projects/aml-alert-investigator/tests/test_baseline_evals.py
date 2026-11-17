import pytest

from amlagent.bankdb import BankDB, load_alerts, load_gold
from amlagent.baseline import investigate_baseline
from amlagent.evals import (
    Section,
    leak_scan,
    render_report,
    score_case,
    scrubber_catch_rate,
    summarise,
)
from amlagent.llm import CassetteProvider, ScriptedProvider, StepRequest
from amlagent.models import CaseResult, Disposition, Finding, Gold, Step
from amlagent.privacy import Vault
from conftest import reply, text


def gold(outcome="escalate", **over):
    base = dict(
        alert_id="AL-001",
        outcome=outcome,
        typology="structuring" if outcome == "escalate" else None,
        difficulty="needs_investigation",
        key_evidence=["T000001", "T000002"],
        scenario="structuring",
    )
    return Gold(**base | over)


def case(
    outcome="escalate_l2",
    tier="lead",
    queue="analyst_review",
    flags=(),
    cite=("T000001",),
    **fields,
):
    vault = Vault("AL-001")
    tokens = [vault.token("TXN", t) for t in cite]
    extra = {
        "escalate_l2": dict(typology="structuring", narrative="CUST_01 structured deposits."),
        "close_false_positive": dict(reason_code="explained_one_off"),
        "needs_info": {},
    }[outcome]
    disposition = Disposition(
        outcome=outcome,
        confidence="high",
        findings=[Finding(statement="s", evidence_ids=tokens)],
        **extra | fields,
    )
    steps = [Step(role=tier, model="claude-haiku-5-5", cost_usd=0.004, latency_s=2.0)]
    result = CaseResult(
        alert_id="AL-001",
        config="routed",
        tier=tier,
        disposition=disposition,
        queue=queue,
        hard_flags=list(flags),
        steps=steps,
        cost_usd=0.004,
        latency_s=2.0,
    )
    return result, vault


def test_correct_escalation_with_partial_evidence():
    row = score_case(*case(), gold(), "dev")
    assert (row.outcome, row.correct, row.unsafe, row.typology_ok) == (
        "escalate",
        True,
        False,
        True,
    )
    assert row.recall == 0.5 and (row.cited, row.cited_valid) == (1, 1)


def test_closing_a_suspicious_alert_is_unsafe():
    row = score_case(*case("close_false_positive", tier="triage"), gold(), "dev")
    assert row.unsafe and row.under_escalated and not row.correct
    lead = score_case(*case("close_false_positive", tier="lead"), gold(), "dev")
    assert lead.unsafe and not lead.under_escalated


def test_manual_investigation_carries_no_recommendation():
    row = score_case(*case("close_false_positive", queue="manual_investigation"), gold(), "dev")
    assert (row.outcome, row.unsafe, row.correct, row.recall) == ("none", False, False, None)


def test_needs_info_on_a_suspicious_alert_is_safe_but_not_correct():
    row = score_case(*case("needs_info"), gold(), "dev")
    assert (row.outcome, row.unsafe, row.correct) == ("needs_info", False, False)


def test_over_escalation_needs_a_clear_unflagged_alert():
    clear = gold("close", difficulty="clear")
    assert score_case(*case("close_false_positive", tier="lead"), clear, "dev").over_escalated
    assert not score_case(*case("close_false_positive", tier="triage"), clear, "dev").over_escalated
    assert not score_case(
        *case("close_false_positive", tier="lead", flags=["LARGE_AMOUNT"]), clear, "dev"
    ).over_escalated


def test_summary_numbers():
    rows = [
        score_case(*case(), gold(), "dev"),
        score_case(*case("close_false_positive", tier="triage"), gold(), "dev"),
        score_case(
            *case("close_false_positive", tier="triage"), gold("close", difficulty="clear"), "dev"
        ),
    ]
    s = summarise(rows)
    assert (s["n"], s["unsafe"], s["under_escalated"], s["over_escalated"]) == (3, 1, 1, 0)
    assert s["accuracy"] == pytest.approx(2 / 3) and s["typology_accuracy"] == 1.0
    assert s["cost_total"] == pytest.approx(0.012) and s["tiers"] == {"lead": 1, "triage": 2}
    assert s["unsafe_ids"] == ["AL-001"] and s["citation_validity"] == 1.0


def test_leak_scan_reads_everything_that_was_sent(tmp_path):
    path = tmp_path / "c.json"
    live = ScriptedProvider([reply(text("a")), reply(text("b"))])
    cassette = CassetteProvider(path, inner=live, record=True)
    cassette.step(
        StepRequest(
            model="claude-haiku-5-5",
            system="s",
            tag="AL-001/triage",
            messages=[{"role": "user", "content": "CUST_01 paid CPTY_02"}],
        )
    )
    assert leak_scan(path, {"Anna Becker"}) == []
    cassette.step(
        StepRequest(
            model="claude-haiku-5-5",
            system="s",
            tag="AL-002/triage",
            messages=[{"role": "user", "content": "customer Anna Becker"}],
        )
    )
    assert leak_scan(path, {"Anna Becker"}) == [("AL-002/triage", ["known_identifier"])]


def test_scrubber_catch_rate_on_the_fixture_bank(db):
    caught, total = scrubber_catch_rate(db, ["Marta Vesely"])
    assert total > 0 and caught == total


def test_baseline_on_the_fixture(db, alert_flagged, alert_clean):
    flagged, vault = investigate_baseline(alert_flagged, db=db)
    assert (flagged.config, flagged.tier) == ("baseline", "baseline")
    assert flagged.disposition.typology == "sanctions_match" and flagged.steps == []
    assert all(t in vault.issued() for f in flagged.disposition.findings for t in f.evidence_ids)
    clean, _ = investigate_baseline(alert_clean, db=db)
    assert clean.disposition.outcome == "close_false_positive" and clean.queue == "analyst_review"


def test_baseline_runs_on_the_whole_dataset():
    db, golds = BankDB(), load_gold()
    rows = [
        score_case(*investigate_baseline(a, db=db), golds[a.alert_id], a.split)
        for a in load_alerts()
    ]
    assert len(rows) == 40
    by_scenario = {r.scenario: r for r in rows}
    assert (
        by_scenario["structuring"].outcome == "escalate"
        and by_scenario["sanctions_match"].outcome == "escalate"
    )


def test_report_shows_measured_and_unmeasured_configurations():
    rows = [score_case(*case(), gold(), "dev"), score_case(*case(), gold(), "heldout")]
    report = render_report(
        [
            Section("baseline", "Rules only", rows),
            Section("nim", "NVIDIA-hosted model in every role", rows, metered=False),
            Section("routed", "Routed", None, note="not recorded"),
        ],
        scrubber=(12, 12),
        leaks={"routed.json": [], "nim.json": [("AL-002/triage", ["iban_pattern"])]},
    )
    assert "## Development set" in report and "## Held-out set" in report
    assert "| Rules only | 1 | 0 |" in report and "trial tier" in report
    assert "Routed: not recorded" in report and "12 of 12" in report
    assert "nim.json: 1 request" in report and "routed.json: none" in report


def test_what_auto_close_would_have_closed_is_reported():
    clear = gold("close", difficulty="clear")
    eligible = score_case(*case("close_false_positive", tier="triage"), clear, "dev")
    by_lead = score_case(*case("close_false_positive", tier="lead"), clear, "dev")
    unsafe = score_case(*case("close_false_positive", tier="triage"), gold(), "dev")
    assert eligible.auto_closable and unsafe.auto_closable and not by_lead.auto_closable
    summary = summarise([eligible, by_lead, unsafe])
    assert (summary["auto_closable"], summary["auto_closable_unsafe"]) == (2, 1)
    report = render_report(
        [Section("routed", "Routed", [eligible, by_lead, unsafe])], scrubber=(1, 1), leaks={}
    )
    assert "Would auto-close" in report and "of them suspicious" in report


def test_a_measured_single_model_run_replaces_the_estimate():
    lead = score_case(*case("escalate_l2", tier="lead"), gold(), "dev")
    routed = Section("routed", "Routed", [lead, lead])
    estimate = render_report([routed], scrubber=(1, 1), leaks={})
    assert "Estimate, not measured" in estimate
    single = Section("opus", "One large model", [lead, lead])
    report = render_report([routed, single], scrubber=(1, 1), leaks={})
    assert "Estimate, not measured" not in report
    assert "one large model on every alert, with no first tier, cost" in report
    assert "2 of 2 outcomes right" in report
