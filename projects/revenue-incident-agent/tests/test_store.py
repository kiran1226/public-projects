import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from revwatch.findings import Citation, Finding
from revwatch.guardrails import Assessment, Held
from revwatch.investigate.base import Case, Investigation
from revwatch.models import Slice
from revwatch.scenarios import Mitigation
from revwatch.store import RunInfo, Store, StoreError, idempotency_key
from revwatch.tools import ToolResult
from revwatch.verify import Check

RUN = RunInfo("hash1", "gbm", "script", {"tool_budget": 12})
COUPON = Mitigation(action="disable_coupon", target={"code": "SOMMER15"})
OPENED = datetime(2026, 8, 25, 13)
CLOCK = [datetime(2026, 11, 16, 9, 0, tzinfo=UTC)]


def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "var" / "queue.sqlite", clock=lambda: CLOCK[0])


def put(
    s: Store, incident_id: str = "INC-1", proposal: Mitigation | None = COUPON, **kw: object
) -> bool:
    citation = Citation(call_id="c1", row="email", column="excess", value=4.5)
    finding = Finding(
        cause="coupon_misconfiguration", change_id="CHG-00042", affected_slice={"country": "DE"},
        evidence=[citation], confidence="high", mitigation=COUPON, summary="Discount rate is up.",
    )  # fmt: skip
    investigation = Investigation(
        finding,
        [
            ToolResult(
                "c1", "breakdown", {"metric": "discount_rate"}, [{"key": "email", "excess": 4.5}]
            )
        ],
    )
    reasons = () if proposal else (Held("LOW_CONFIDENCE", "confidence is medium"),)
    assessment = Assessment(
        reasons,
        proposal,
        str(kw.get("severity", "page")),
        float(kw.get("impact", 3000.0)),
        "marketing",
        (Check(citation, True),),
    )  # type: ignore[arg-type]
    case = Case(incident_id, "discount_rate", Slice.of(country="DE", channel="email"), 1, 2054)
    return s.record(RUN, case, OPENED, investigation, assessment)


def test_an_incident_is_stored_with_what_produced_it(tmp_path: Path) -> None:
    s = store(tmp_path)
    assert put(s)
    record = s.get("INC-1")
    assert (record.world_hash, record.detector, record.investigator) == ("hash1", "gbm", "script")
    assert record.status == "open" and record.opened_at == OPENED
    assert record.slice == {"channel": "email", "country": "DE"} and record.direction == 1
    assert record.finding.cause == "coupon_misconfiguration" and record.proposal == COUPON
    assert (record.evidence_ok, record.evidence_total, record.tool_calls) == (1, 1, 1)
    assert record.trace[0]["rows"] == [{"key": "email", "excess": 4.5}]
    assert [e["kind"] for e in s.events("INC-1")] == ["recorded"]
    with pytest.raises(StoreError, match="no incident INC-9"):
        s.get("INC-9")


def test_the_queue_lists_the_most_urgent_first(tmp_path: Path) -> None:
    s = store(tmp_path)
    put(s, "INC-digest", severity="digest", impact=50.0)
    put(s, "INC-page-small", severity="page", impact=2500.0)
    put(s, "INC-ticket", severity="ticket", impact=900.0)
    put(s, "INC-page-big", severity="page", impact=9000.0)
    assert [r.incident_id for r in s.incidents()] == [
        "INC-page-big",
        "INC-page-small",
        "INC-ticket",
        "INC-digest",
    ]


def test_approving_a_proposal_writes_the_decision_and_the_pending_action_together(
    tmp_path: Path,
) -> None:
    s = store(tmp_path)
    put(s)
    action = s.approve("INC-1", "j.meier", "looks right")
    assert action is not None and action.status == "pending"
    assert (action.action, action.target, action.reviewer) == (
        "disable_coupon",
        {"code": "SOMMER15"},
        "j.meier",
    )
    assert (
        action.idempotency_key
        == idempotency_key("INC-1", COUPON)
        == "INC-1:disable_coupon:code=SOMMER15"
    )
    assert s.get("INC-1").status == "action_pending"
    assert [(d["decision"], d["reviewer"], d["note"]) for d in s.decisions("INC-1")] == [
        ("approve", "j.meier", "looks right")
    ]
    assert s.confirmed("INC-1")


def test_approving_twice_gives_the_same_action_and_one_decision(tmp_path: Path) -> None:
    s = store(tmp_path)
    put(s)
    first = s.approve("INC-1", "j.meier")
    second = s.approve("INC-1", "j.meier")
    assert first == second and len(s.decisions("INC-1")) == 1
    assert "approve_repeated" in [e["kind"] for e in s.events("INC-1")]
    s.finish_action(first, True, "changed")  # type: ignore[arg-type]
    assert s.get("INC-1").status == "mitigated"
    assert s.approve("INC-1", "j.meier") is None  # done: nothing left to send


def test_an_incident_without_a_proposal_is_approved_with_no_action(tmp_path: Path) -> None:
    s = store(tmp_path)
    put(s, proposal=None)
    assert s.approve("INC-1", "j.meier") is None
    assert s.get("INC-1").status == "approved" and s.action_for("INC-1") is None


def test_a_failed_action_keeps_the_approval_and_can_be_retried(tmp_path: Path) -> None:
    s = store(tmp_path)
    put(s)
    action = s.approve("INC-1", "j.meier")
    assert action is not None
    s.log_attempt(action, {"attempt": 1, "status_code": 503})
    s.finish_action(action, False, "admin service returned 503")
    assert s.get("INC-1").status == "action_failed"
    again = s.approve("INC-1", "j.meier")
    assert again is not None and again.idempotency_key == action.idempotency_key
    assert (again.status, again.attempts, again.detail) == (
        "failed",
        1,
        "admin service returned 503",
    )
    kinds = [e["kind"] for e in s.events("INC-1")]
    assert kinds == ["recorded", "approve", "admin_call", "action_failed", "approve_repeated"]


def test_a_correction_or_rejection_never_creates_an_action(tmp_path: Path) -> None:
    s = store(tmp_path)
    put(s, "INC-1")
    put(s, "INC-2")
    s.correct("INC-1", "j.meier", "price_error", "it was the price import")
    s.reject("INC-2", "j.meier", "test traffic")
    assert s.get("INC-1").status == "corrected" and s.action_for("INC-1") is None
    assert s.get("INC-2").status == "rejected" and s.action_for("INC-2") is None
    assert s.approve("INC-1", "someone") is None and s.action_for("INC-1") is None
    assert s.confirmed("INC-1") and not s.confirmed("INC-2")
    with pytest.raises(StoreError, match="already corrected"):
        s.reject("INC-1", "j.meier")
    with pytest.raises(StoreError, match="needs the reviewer's name"):
        put(s, "INC-3")
        s.reject("INC-3", "  ")
    assert s.get("INC-3").status == "open"  # the failed decision changed nothing


def test_a_rerun_replaces_open_incidents_and_leaves_decided_ones_alone(tmp_path: Path) -> None:
    s = store(tmp_path)
    put(s, "INC-open", severity="page")
    put(s, "INC-done", severity="page")
    s.reject("INC-done", "j.meier")
    assert put(s, "INC-open", severity="ticket") is True
    assert put(s, "INC-done", severity="ticket") is False
    assert s.get("INC-open").severity == "ticket"
    assert (s.get("INC-done").severity, s.get("INC-done").status) == ("page", "rejected")
    assert [e["kind"] for e in s.events("INC-done")][-1] == "rerun_skipped"


def test_the_event_log_only_grows_and_decisions_survive_a_restart(tmp_path: Path) -> None:
    s = store(tmp_path)
    put(s)
    s.approve("INC-1", "j.meier")
    before = s.events()
    s.close()
    again = store(tmp_path)
    assert again.events() == before and again.get("INC-1").status == "action_pending"
    ids = [e["id"] for e in again.events()]
    assert (
        ids == sorted(ids)
        and [e["at"] for e in again.events()] == ["2026-11-16T09:00:00+00:00"] * 2
    )
    with sqlite3.connect(again.path) as raw:
        assert raw.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 1


def test_stats_count_the_queue_and_the_override_rate(tmp_path: Path) -> None:
    s = store(tmp_path)
    put(s, "INC-1")
    put(s, "INC-2", proposal=None, severity="ticket")
    put(s, "INC-3")
    assert s.stats()["override_rate"] is None
    s.approve("INC-1", "j.meier")
    s.correct("INC-3", "j.meier", "price_error")
    stats = s.stats()
    assert stats["incidents"] == 3 and stats["with_proposal"] == 2 and stats["decided"] == 2
    assert stats["by_severity"] == {"page": 2, "ticket": 1}
    assert stats["by_status"] == {"action_pending": 1, "corrected": 1, "open": 1}
    assert stats["held"] == {"LOW_CONFIDENCE": 1} and stats["override_rate"] == 0.5


def test_a_corrected_cause_is_exported_as_a_label(tmp_path: Path) -> None:
    s = store(tmp_path)
    put(s)
    assert s.corrections() == []
    s.correct("INC-1", "j.meier", "price_error", "price import, not the coupon")
    assert s.corrections() == [
        {
            "id": "review-INC-1", "kind": "incident", "cause": "price_error",
            "start": "2026-08-25T13:00", "slice": {"channel": "email", "country": "DE"},
            "metric": "discount_rate", "world_hash": "hash1", "reviewer": "j.meier",
            "note": "price import, not the coupon", "found_by_system": "coupon_misconfiguration",
        }
    ]  # fmt: skip
