from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from editwatch.config import CATEGORIES, PROJECT_ROOT, load_settings
from editwatch.models import Prediction, Verdict
from editwatch.review import (
    COMMENT_MAX,
    DEFAULT_PATH,
    Review,
    ReviewLog,
    Summary,
    fine,
    path_for,
    places,
    problem,
    summary,
    unsure,
)

VERSION = "abc1234567"
OTHER = "fff0000000"


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 17, hour, minute, tzinfo=UTC)


def prediction(
    rev_id: int = 501, risk: float = 0.5, *, version: str = VERSION, model: bool = True
) -> Prediction:
    """A row of the prediction log. With `model` it is one the model decided."""
    return Prediction(
        rev_id=rev_id,
        made_at=at(6),
        written_at=at(6, 1),
        system_version=version,
        config="cascade",
        verdict=Verdict(
            risk=risk,
            tier1=0.4,
            model_risk=80 if model else None,
            category="vandalism" if model else None,
            source="model" if model else "tier1",
        ),
    )


@pytest.fixture
def log(tmp_path) -> ReviewLog:
    return ReviewLog(tmp_path / "corrections" / "reviews.jsonl")


def test_an_empty_log_reads_as_nothing_and_creates_no_file(log):
    assert log.read() == []
    assert log.latest() == {}
    assert log.for_edit(501) == []
    assert not log.path.exists()


def test_a_review_round_trips_as_one_json_line(log):
    made = log.add(problem(prediction(), "vandalism", comment="Replaces a date.", now=at(8)))
    lines = log.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "rev_id": 501,
        "system_version": VERSION,
        "verdict": "problem",
        "category": "vandalism",
        "decided_at": "2026-11-17T08:00:00Z",
        "comment": "Replaces a date.",
    }
    assert log.read() == [made]


def test_a_review_has_no_field_for_a_person():
    assert set(Review.model_fields) == {
        "rev_id", "system_version", "verdict", "category", "decided_at", "comment"
    }  # fmt: skip


def test_the_three_decisions():
    called = problem(prediction(), "content_removal", now=at(8))
    assert (called.verdict, called.category) == ("problem", "content_removal")
    # A problem the reviewer does not name is still a problem.
    assert problem(prediction(), now=at(8)).category is None
    assert (fine(prediction(), now=at(8)).verdict, fine(prediction(), now=at(8)).category) == (
        "fine",
        None,
    )
    left_open = unsure(prediction(), comment="Needs the source.", now=at(8))
    assert (left_open.verdict, left_open.category) == ("unsure", None)
    assert left_open.comment == "Needs the source."
    assert left_open.key == (501, VERSION)


@pytest.mark.parametrize("category", [c for c in CATEGORIES if c != "none"])
def test_a_problem_may_carry_any_category_of_the_fixed_list(category):
    assert problem(prediction(), category, now=at(8)).category == category


def test_only_the_three_verdicts_and_the_listed_categories_exist():
    fields = problem(prediction(), "vandalism", now=at(8)).model_dump()
    with pytest.raises(ValidationError):
        Review(**{**fields, "verdict": "maybe"})
    with pytest.raises(ValidationError, match="must be one of"):
        problem(prediction(), "rude", now=at(8))
    with pytest.raises(ValidationError, match='cannot have the category "none"'):
        problem(prediction(), "none", now=at(8))
    with pytest.raises(ValidationError, match="Only an edit called a problem"):
        Review(**{**fields, "verdict": "fine"})
    with pytest.raises(ValidationError, match="Only an edit called a problem"):
        Review(**{**fields, "verdict": "unsure"})


def test_the_latest_decision_wins_and_the_earlier_line_stays(log):
    log.add(fine(prediction(), now=at(8)))
    log.add(fine(prediction(502), now=at(9)))
    log.add(problem(prediction(), "vandalism", comment="Read it again.", now=at(10)))

    latest = log.latest()
    assert set(latest) == {(501, VERSION), (502, VERSION)}
    assert latest[(501, VERSION)].verdict == "problem"
    assert latest[(502, VERSION)].verdict == "fine"
    assert [r.verdict for r in log.read()] == ["fine", "fine", "problem"]
    assert len(log.path.read_text(encoding="utf-8").splitlines()) == 3


def test_the_newest_decision_wins_whatever_the_order_in_the_file(log):
    log.add(problem(prediction(), now=at(10)))
    log.add(fine(prediction(), now=at(8)))
    assert log.latest()[(501, VERSION)].verdict == "problem"

    # Two decisions made at the same moment: the one written later is in force.
    log.add(unsure(prediction(), now=at(10)))
    assert log.latest()[(501, VERSION)].verdict == "unsure"


def test_each_system_version_of_an_edit_has_its_own_decision(log):
    log.add(fine(prediction(), now=at(8)))
    log.add(problem(prediction(version=OTHER), now=at(9)))
    log.add(fine(prediction(502), now=at(9)))

    latest = log.latest()
    assert latest[(501, VERSION)].verdict == "fine"
    assert latest[(501, OTHER)].verdict == "problem"
    assert [r.system_version for r in log.for_edit(501)] == [VERSION, OTHER]
    assert log.for_edit(999) == []


def test_adding_never_rewrites_what_is_already_in_the_file(log):
    log.add(fine(prediction(), now=at(8)))
    before = log.path.read_bytes()
    log.add(problem(prediction(), "other", now=at(9)))
    ReviewLog(log.path).add(unsure(prediction(502), now=at(9)))
    after = log.path.read_bytes()
    assert after.startswith(before)
    assert after.count(b"\n") == 3
    assert {key[0] for key in log.latest()} == {501, 502}


def test_the_time_zone_of_a_decision_is_kept(log):
    plus_three = timezone(timedelta(hours=3))
    moment = datetime(2026, 11, 17, 11, 30, tzinfo=plus_three)
    log.add(fine(prediction(), now=moment))

    assert '"decided_at": "2026-11-17T11:30:00+03:00"' in log.path.read_text(encoding="utf-8")
    read = log.read()[0].decided_at
    assert read == moment
    assert read.utcoffset() == timedelta(hours=3)


def test_a_decision_needs_a_time_zone_and_defaults_to_now_in_utc():
    with pytest.raises(ValidationError, match="timezone"):
        fine(prediction(), now=datetime(2026, 11, 17, 8, 0))
    before = datetime.now(UTC)
    made = fine(prediction())
    assert before <= made.decided_at <= datetime.now(UTC)
    assert made.decided_at.utcoffset() == timedelta(0)


def test_a_comment_may_be_500_characters_and_no_longer(log):
    assert len(fine(prediction(), comment="x" * COMMENT_MAX, now=at(8)).comment) == 500
    with pytest.raises(ValidationError, match="at most 500"):
        fine(prediction(), comment="x" * (COMMENT_MAX + 1), now=at(8))
    assert log.read() == []


def test_a_comment_is_stored_without_outer_spaces_and_an_empty_one_is_none(log):
    assert unsure(prediction(), comment="  Needs a source. \n", now=at(8)).comment == (
        "Needs a source."
    )
    assert fine(prediction(), comment="   ", now=at(8)).comment is None
    assert fine(prediction(), comment="", now=at(8)).comment is None
    # Other alphabets survive the file as written, not as escapes.
    log.add(problem(prediction(), comment="Yanlış tarih: 1887 değil. Żółć.", now=at(8)))
    assert log.read()[0].comment == "Yanlış tarih: 1887 değil. Żółć."
    assert "Żółć" in log.path.read_text(encoding="utf-8")


def test_summary_counts_each_verdict():
    reviews = [
        problem(prediction(501), "vandalism", now=at(8)),
        problem(prediction(502), now=at(8)),
        fine(prediction(503), now=at(8)),
        unsure(prediction(504), now=at(8)),
    ]
    assert summary(reviews) == Summary(problem=2, fine=1, unsure=1)
    assert summary(reviews).decided == 4
    assert summary(reviews).placed == 0
    assert summary([]) == Summary()
    assert summary([]).decided == 0


def test_summary_counts_which_tier_had_decided_the_reviewed_edits():
    queue = [
        prediction(501, 0.9),
        prediction(502, 0.8),
        prediction(503, 0.3, model=False),
        prediction(504, 0.2, model=False),
    ]
    reviews = [
        fine(queue[0], now=at(8)),
        unsure(queue[1], now=at(8)),
        fine(queue[2], now=at(8)),
        # Its entry is not in the queue passed: counted under its verdict only.
        fine(prediction(999), now=at(8)),
    ]
    counts = summary(reviews, queue)
    assert counts == Summary(fine=3, unsure=1, by_model=2, by_tier1=1)
    assert (counts.decided, counts.placed) == (4, 3)


def test_summary_counts_the_problems_that_stood_in_the_top_tenth_of_the_queue():
    # Twenty entries, riskiest first: the top 10% is the first two.
    queue = [prediction(600 + n, 0.95 - n * 0.04) for n in range(20)]
    reviews = [
        problem(queue[0], "vandalism", now=at(8)),
        problem(queue[1], now=at(8)),
        problem(queue[2], now=at(8)),
        problem(queue[19], "content_removal", now=at(8)),
        # Not a problem: its place does not matter.
        fine(queue[3], now=at(8)),
    ]
    counts = summary(reviews, queue)
    assert (counts.problem, counts.fine) == (4, 1)
    assert (counts.problems_placed, counts.problems_in_top) == (4, 2)
    assert counts.by_model == 5


def test_the_top_tenth_is_rounded_up_and_each_version_is_its_own_queue():
    # Five entries: the top 10% is one entry, not none.
    short = [prediction(700 + n, 0.9 - n * 0.1) for n in range(5)]
    other = [prediction(700 + n, 0.1 + n * 0.1, version=OTHER) for n in range(5)]
    standing = places([*short, *other])
    assert standing[(700, VERSION)] == (1, 5)
    assert standing[(704, VERSION)] == (5, 5)
    # Under the other version the order is the reverse.
    assert standing[(704, OTHER)] == (1, 5)

    reviews = [problem(short[0], now=at(8)), problem(other[0], now=at(8))]
    counts = summary(reviews, [*short, *other])
    assert (counts.problems_placed, counts.problems_in_top) == (2, 1)


def test_equal_risks_are_placed_by_revision_as_the_queue_places_them():
    tied = [prediction(803, 0.5), prediction(801, 0.5), prediction(802, 0.7)]
    standing = places(tied)
    assert [standing[(rev, VERSION)][0] for rev in (802, 801, 803)] == [1, 2, 3]


def test_summary_over_the_latest_decisions_counts_a_changed_mind_once(log):
    queue = [prediction(501, 0.9), prediction(502, 0.1, model=False)]
    log.add(fine(queue[0], now=at(8)))
    log.add(problem(queue[0], "vandalism", now=at(9)))
    log.add(fine(queue[1], now=at(9)))
    assert summary(log.latest().values(), queue) == Summary(
        problem=1, fine=1, by_model=1, by_tier1=1, problems_placed=1, problems_in_top=1
    )
    assert summary(log.read()) == Summary(problem=1, fine=2)


def test_the_default_file_is_under_the_ignored_corrections_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("EDITWATCH_HOME", raising=False)
    assert DEFAULT_PATH == PROJECT_ROOT / "data" / "corrections" / "reviews.jsonl"
    assert ReviewLog().path == DEFAULT_PATH
    assert path_for(load_settings()) == DEFAULT_PATH
    # The review file moves with the logs.
    monkeypatch.setenv("EDITWATCH_HOME", str(tmp_path))
    assert path_for(load_settings()) == tmp_path / "data" / "corrections" / "reviews.jsonl"
