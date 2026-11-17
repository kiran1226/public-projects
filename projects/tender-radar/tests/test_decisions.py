from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from tenderradar.config import PROJECT_ROOT, load_settings
from tenderradar.decisions import DEFAULT_PATH, Decision, DecisionLog, path_for

DAY = date(2026, 11, 17)


def at(hour: int) -> datetime:
    return datetime(2026, 11, 17, hour, 0, tzinfo=UTC)


@pytest.fixture
def log(tmp_path) -> DecisionLog:
    return DecisionLog(tmp_path / "corrections" / "decisions.jsonl")


def test_an_empty_log_reads_as_nothing(log):
    assert log.all() == []
    assert log.latest() == {}
    assert not log.path.exists()


def test_record_returns_the_decision_and_appends_one_json_line(log):
    made = log.record("300100-2026", DAY, "bid", "Core product, known buyer.", now=at(8))
    assert made == Decision(
        publication_number="300100-2026",
        day=DAY,
        decision="bid",
        reason="Core product, known buyer.",
        decided_at=at(8),
    )
    lines = log.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "publication_number": "300100-2026",
        "day": "2026-11-17",
        "decision": "bid",
        "reason": "Core product, known buyer.",
        "decided_at": "2026-11-17T08:00:00Z",
    }


def test_decisions_are_read_back_in_the_order_they_were_written(log):
    log.record("300100-2026", DAY, "bid", now=at(8))
    log.record("300200-2026", DAY, "skip", "Turnover requirement too high.", now=at(9))
    assert [d.publication_number for d in log.all()] == ["300100-2026", "300200-2026"]
    assert [d.decision for d in log.all()] == ["bid", "skip"]


def test_a_later_decision_supersedes_but_the_earlier_line_stays(log):
    log.record("300100-2026", DAY, "bid", now=at(8))
    log.record("300200-2026", DAY, "bid", now=at(9))
    log.record("300100-2026", DAY, "skip", "The buyer withdrew lot 1.", now=at(10))

    latest = log.latest()
    assert set(latest) == {"300100-2026", "300200-2026"}
    assert latest["300100-2026"].decision == "skip"
    assert latest["300100-2026"].reason == "The buyer withdrew lot 1."
    assert latest["300200-2026"].decision == "bid"

    assert len(log.all()) == 3
    assert log.all()[0].decision == "bid"
    assert len(log.path.read_text(encoding="utf-8").splitlines()) == 3


def test_recording_never_rewrites_what_is_already_in_the_file(log):
    log.record("300100-2026", DAY, "bid", now=at(8))
    before = log.path.read_text(encoding="utf-8")
    log.record("300100-2026", DAY, "skip", "Changed our mind.", now=at(9))
    assert log.path.read_text(encoding="utf-8").startswith(before)


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_a_skip_without_a_reason_is_refused_and_nothing_is_written(log, reason):
    with pytest.raises(ValueError, match="reason"):
        log.record("300100-2026", DAY, "skip", reason)
    assert log.all() == []
    assert not log.path.exists()


def test_a_bid_needs_no_reason(log):
    assert log.record("300100-2026", DAY, "bid").reason == ""


def test_only_bid_and_skip_are_decisions(log):
    with pytest.raises(ValueError, match="bid"):
        log.record("300100-2026", DAY, "ask_person", "Not sure.")
    assert log.all() == []


def test_the_reason_is_stored_without_outer_spaces(log):
    assert log.record("300100-2026", DAY, "skip", "  Too far away. \n").reason == "Too far away."


def test_decided_at_defaults_to_now_in_utc(log):
    before = datetime.now(UTC)
    made = log.record("300100-2026", DAY, "bid")
    assert before <= made.decided_at <= datetime.now(UTC)
    assert log.all()[0].decided_at == made.decided_at


def test_text_in_other_alphabets_survives_the_round_trip(log):
    log.record("300100-2026", DAY, "skip", "Umsatz zu hoch: 1 000 000 € gefordert, Łódź.")
    assert log.latest()["300100-2026"].reason == "Umsatz zu hoch: 1 000 000 € gefordert, Łódź."
    assert "Łódź" in log.path.read_text(encoding="utf-8")


def test_two_logs_on_the_same_file_see_each_other(log):
    log.record("300100-2026", DAY, "bid", now=at(8))
    other = DecisionLog(log.path)
    other.record("300200-2026", DAY, "bid", now=at(9))
    assert set(log.latest()) == {"300100-2026", "300200-2026"}


def test_the_default_file_is_under_the_ignored_corrections_folder(monkeypatch):
    monkeypatch.delenv("TENDERRADAR_HOME", raising=False)
    assert DEFAULT_PATH == PROJECT_ROOT / "data" / "corrections" / "decisions.jsonl"
    assert DecisionLog().path == DEFAULT_PATH
    assert path_for(load_settings()) == DEFAULT_PATH


def test_the_decision_file_moves_with_the_logs(monkeypatch, tmp_path):
    monkeypatch.setenv("TENDERRADAR_HOME", str(tmp_path))
    assert path_for(load_settings()) == tmp_path / "data" / "corrections" / "decisions.jsonl"
