from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from triagedesk.config import PROJECT_ROOT, load_settings
from triagedesk.models import LabelPick, Prediction, Proposal
from triagedesk.review import (
    COMMENT_MAX,
    DEFAULT_PATH,
    Review,
    ReviewLog,
    Summary,
    accept,
    correct,
    path_for,
    reject,
    summary,
)

VERSION = "abc1234567"


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 17, hour, minute, tzinfo=UTC)


def prediction(number: int = 501, version: str = VERSION, **proposal) -> Prediction:
    fields = dict(
        labels=[
            LabelPick(name="topic:physics", confidence="high"),
            LabelPick(name="platform:web", confidence="medium"),
            LabelPick(name="topic:gui", confidence="low"),
        ],
        duplicate_of=312,
        duplicate_confidence="medium",
        person=["uncertain_label", "possible_duplicate"],
        source="model",
    )
    return Prediction(
        number=number,
        created_at=at(6),
        written_at=at(7),
        system_version=version,
        config="model",
        proposal=Proposal(**{**fields, **proposal}),
        candidates=[312, 290],
    )


@pytest.fixture
def log(tmp_path) -> ReviewLog:
    return ReviewLog(tmp_path / "corrections" / "reviews.jsonl")


def test_an_empty_log_reads_as_nothing_and_creates_no_file(log):
    assert log.read() == []
    assert log.latest() == {}
    assert log.for_issue(501) == []
    assert not log.path.exists()


def test_a_review_round_trips_as_one_json_line(log):
    made = log.add(accept(prediction(), comment="Matches the report.", now=at(8)))
    lines = log.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "number": 501,
        "system_version": VERSION,
        "config": "model",
        "verdict": "accepted",
        "labels": ["platform:web", "topic:physics"],
        "duplicate_of": 312,
        "decided_at": "2026-11-17T08:00:00Z",
        "comment": "Matches the report.",
    }
    assert log.read() == [made]


def test_accepting_keeps_the_labels_proposed_at_medium_or_high_confidence():
    made = accept(prediction(), now=at(8))
    assert made.verdict == "accepted"
    assert made.labels == ["platform:web", "topic:physics"]
    assert "topic:gui" not in made.labels
    assert made.duplicate_of == 312
    assert made.comment is None


def test_a_correction_records_the_reviewers_answer_sorted_and_without_repeats():
    made = correct(prediction(), ["topic:physics", "topic:audio", "topic:physics"], None, now=at(8))
    assert made.verdict == "corrected"
    assert made.labels == ["topic:audio", "topic:physics"]
    assert made.duplicate_of is None


def test_a_correction_that_changes_nothing_is_an_acceptance():
    made = correct(prediction(), ["topic:physics", "platform:web"], 312, now=at(8))
    assert made.verdict == "accepted"
    assert made == accept(prediction(), now=at(8))


def test_a_rejection_records_no_answer():
    made = reject(prediction(), comment="Not a physics problem at all.", now=at(8))
    assert (made.verdict, made.labels, made.duplicate_of) == ("rejected", [], None)
    assert made.comment == "Not a physics problem at all."


def test_the_latest_decision_wins_and_the_earlier_line_stays(log):
    log.add(accept(prediction(), now=at(8)))
    log.add(accept(prediction(502), now=at(9)))
    log.add(reject(prediction(), comment="Read it again: wrong area.", now=at(10)))

    latest = log.latest()
    assert set(latest) == {(501, VERSION), (502, VERSION)}
    assert latest[(501, VERSION)].verdict == "rejected"
    assert latest[(502, VERSION)].verdict == "accepted"
    assert [r.verdict for r in log.read()] == ["accepted", "accepted", "rejected"]
    assert len(log.path.read_text(encoding="utf-8").splitlines()) == 3


def test_the_newest_decision_wins_whatever_the_order_in_the_file(log):
    log.add(reject(prediction(), now=at(10)))
    log.add(accept(prediction(), now=at(8)))
    assert log.latest()[(501, VERSION)].verdict == "rejected"

    # Two decisions made at the same moment: the one written later is in force.
    log.add(accept(prediction(), now=at(10)))
    assert log.latest()[(501, VERSION)].verdict == "accepted"


def test_each_system_version_of_an_issue_has_its_own_decision(log):
    log.add(accept(prediction(), now=at(8)))
    log.add(reject(prediction(version="fff0000000"), now=at(9)))
    log.add(accept(prediction(502), now=at(9)))

    latest = log.latest()
    assert latest[(501, VERSION)].verdict == "accepted"
    assert latest[(501, "fff0000000")].verdict == "rejected"
    assert [r.system_version for r in log.for_issue(501)] == [VERSION, "fff0000000"]
    assert log.for_issue(999) == []


def test_adding_never_rewrites_what_is_already_in_the_file(log):
    log.add(accept(prediction(), now=at(8)))
    before = log.path.read_bytes()
    log.add(correct(prediction(), ["topic:audio"], None, now=at(9)))
    log.add(reject(prediction(502), now=at(9)))
    after = log.path.read_bytes()
    assert after.startswith(before)
    assert after.count(b"\n") == 3


def test_two_logs_on_the_same_file_see_each_other(log):
    log.add(accept(prediction(), now=at(8)))
    ReviewLog(log.path).add(accept(prediction(502), now=at(9)))
    assert {key[0] for key in log.latest()} == {501, 502}


def test_the_time_zone_of_a_decision_is_kept(log):
    plus_three = timezone(timedelta(hours=3))
    moment = datetime(2026, 11, 17, 11, 30, tzinfo=plus_three)
    log.add(accept(prediction(), now=moment))

    assert '"decided_at": "2026-11-17T11:30:00+03:00"' in log.path.read_text(encoding="utf-8")
    read = log.read()[0].decided_at
    assert read == moment
    assert read.utcoffset() == timedelta(hours=3)


def test_a_decision_without_a_time_zone_is_refused():
    with pytest.raises(ValidationError, match="timezone"):
        accept(prediction(), now=datetime(2026, 11, 17, 8, 0))


def test_decided_at_defaults_to_now_in_utc():
    before = datetime.now(UTC)
    made = accept(prediction())
    assert before <= made.decided_at <= datetime.now(UTC)
    assert made.decided_at.utcoffset() == timedelta(0)


def test_a_comment_may_be_500_characters_and_no_longer(log):
    assert len(accept(prediction(), comment="x" * COMMENT_MAX, now=at(8)).comment) == 500
    with pytest.raises(ValidationError, match="at most 500"):
        accept(prediction(), comment="x" * (COMMENT_MAX + 1), now=at(8))
    assert log.read() == []


def test_a_comment_is_stored_without_outer_spaces_and_an_empty_one_is_none():
    assert reject(prediction(), comment="  Wrong area. \n", now=at(8)).comment == "Wrong area."
    assert accept(prediction(), comment="   ", now=at(8)).comment is None
    assert accept(prediction(), comment="", now=at(8)).comment is None


def test_text_in_other_alphabets_survives_the_round_trip(log):
    log.add(reject(prediction(), comment="Yanlış alan: ses değil, fizik. Żółć.", now=at(8)))
    assert log.read()[0].comment == "Yanlış alan: ses değil, fizik. Żółć."
    assert "Żółć" in log.path.read_text(encoding="utf-8")


def test_only_the_three_verdicts_exist_and_an_issue_is_not_its_own_duplicate():
    fields = accept(prediction(), now=at(8)).model_dump()
    with pytest.raises(ValidationError):
        Review(**{**fields, "verdict": "maybe"})
    with pytest.raises(ValidationError, match="duplicate of itself"):
        correct(prediction(), ["topic:physics"], 501, now=at(8))
    with pytest.raises(ValidationError):
        correct(prediction(), ["topic:physics"], 0, now=at(8))


def test_summary_counts_each_verdict():
    reviews = [
        accept(prediction(501), now=at(8)),
        accept(prediction(502), now=at(8)),
        correct(prediction(503), ["topic:audio"], None, now=at(8)),
        reject(prediction(504), now=at(8)),
    ]
    assert summary(reviews) == Summary(accepted=2, corrected=1, rejected=1)
    assert summary(reviews).decided == 4
    assert summary([]) == Summary()
    assert summary([]).decided == 0


def test_summary_counts_labels_added_and_removed_against_the_proposals():
    first, second, third = prediction(501), prediction(502), prediction(503)
    reviews = [
        # platform:web removed, topic:audio and topic:input added, duplicate dropped.
        correct(first, ["topic:physics", "topic:audio", "topic:input"], None, now=at(8)),
        # Labels kept, another original named.
        correct(second, ["topic:physics", "platform:web"], 290, now=at(8)),
        # Its proposal is not passed: counted as corrected, left out of the label counts.
        correct(third, [], None, now=at(8)),
        accept(prediction(504), now=at(8)),
    ]
    proposals = {(p.number, p.system_version): p.proposal for p in (first, second)}

    assert summary(reviews, proposals) == Summary(
        accepted=1,
        corrected=3,
        compared=2,
        labels_added=2,
        labels_removed=1,
        duplicates_changed=2,
    )


def test_summary_over_the_latest_decisions_counts_a_changed_mind_once(log):
    log.add(accept(prediction(), now=at(8)))
    log.add(reject(prediction(), now=at(9)))
    log.add(accept(prediction(502), now=at(9)))
    assert summary(log.latest().values()) == Summary(accepted=1, rejected=1)
    assert summary(log.read()) == Summary(accepted=2, rejected=1)


def test_the_default_file_is_under_the_ignored_corrections_folder(monkeypatch):
    monkeypatch.delenv("TRIAGEDESK_HOME", raising=False)
    assert DEFAULT_PATH == PROJECT_ROOT / "data" / "corrections" / "reviews.jsonl"
    assert ReviewLog().path == DEFAULT_PATH
    assert path_for(load_settings()) == DEFAULT_PATH


def test_the_review_file_moves_with_the_logs(monkeypatch, tmp_path):
    monkeypatch.setenv("TRIAGEDESK_HOME", str(tmp_path))
    assert path_for(load_settings()) == tmp_path / "data" / "corrections" / "reviews.jsonl"
