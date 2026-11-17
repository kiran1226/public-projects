"""Truth (what the maintainers did) and the gate (what the desk may say)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from triagedesk.feed import RateLimited
from triagedesk.gate import (
    DUPLICATE_NOT_A_CANDIDATE,
    DUPLICATE_NOT_OLDER,
    GATE_CHANGED_ANSWER,
    LABEL_NOT_IN_TAXONOMY,
    MODEL_UNAVAILABLE,
    POSSIBLE_DUPLICATE,
    TOO_MANY_LABELS,
    UNCERTAIN_LABEL,
    gate,
)
from triagedesk.models import LabelDefinition, LabelPick, Outcome, Taxonomy, TimelineEvent
from triagedesk.truth import (
    NOT_FETCHED,
    OUTSIDE,
    RESOLVED,
    UNRESOLVED,
    OutcomeLog,
    collect,
    fetch_timelines,
    outcome_for,
    resolve_original,
)

from conftest import T0, make_issue

CLOSE = T0 + timedelta(days=2)


def taxonomy(*names: str) -> Taxonomy:
    names = names or ("topic:gui", "topic:editor", "topic:physics", "topic:audio", "platform:web")
    return Taxonomy(
        repo="example/engine",
        labels=[
            LabelDefinition(name=n, group="area" if n.startswith("topic:") else "platform")
            for n in names
        ],
    )


def comment(minutes_before_close: float, refs=(), outside=(), duplicate=False) -> TimelineEvent:
    return TimelineEvent(
        event="commented",
        created_at=CLOSE - timedelta(minutes=minutes_before_close),
        refs=list(refs),
        outside_refs=list(outside),
        says_duplicate=duplicate,
    )


def closed(reason="duplicate") -> TimelineEvent:
    return TimelineEvent(event="closed", created_at=CLOSE, state_reason=reason)


def older(ref: int) -> bool:
    return ref < 100


def test_a_comment_that_says_duplicate_and_names_an_older_issue_resolves_it():
    events = [comment(5, refs=[42], duplicate=True), closed()]
    assert resolve_original(100, events, older) == (42, RESOLVED)


def test_the_last_duplicate_comment_before_the_close_wins():
    events = [
        comment(600, refs=[10], duplicate=True),
        comment(5, refs=[42], duplicate=True),
        closed(),
    ]
    assert resolve_original(100, events, older) == (42, RESOLVED)


def test_a_reference_to_the_issue_itself_or_a_newer_one_is_ignored():
    events = [comment(5, refs=[100, 250, 42], duplicate=True), closed()]
    assert resolve_original(100, events, older) == (42, RESOLVED)


def test_a_closing_comment_with_one_older_issue_resolves_without_the_word_duplicate():
    events = [comment(900, refs=[7, 8]), comment(2, refs=[42]), closed()]
    assert resolve_original(100, events, older) == (42, RESOLVED)


def test_one_older_issue_named_anywhere_in_the_discussion_resolves_it():
    events = [comment(3000, refs=[42]), comment(600), closed()]
    assert resolve_original(100, events, older) == (42, RESOLVED)


def test_a_comment_shortly_after_the_close_counts():
    events = [closed(), comment(-20, refs=[42])]
    assert resolve_original(100, events, older) == (42, RESOLVED)


def test_a_comment_long_after_the_close_does_not_count():
    events = [closed(), comment(-3 * 24 * 60, refs=[42])]
    assert resolve_original(100, events, older) == (None, UNRESOLVED)


def test_two_older_issues_with_nothing_to_choose_between_them_stay_unresolved():
    events = [comment(3000, refs=[7]), comment(2000, refs=[7, 8]), closed()]
    assert resolve_original(100, events, older) == (None, UNRESOLVED)


def test_a_duplicate_of_an_issue_in_another_repository_is_outside():
    events = [comment(5, outside=["example/proposals#3126"], duplicate=True), closed()]
    assert resolve_original(100, events, older) == (None, OUTSIDE)


def test_an_outside_reference_does_not_hide_an_older_issue_of_this_tracker():
    events = [
        comment(50, outside=["example/proposals#1"]),
        comment(5, refs=[42], duplicate=True),
        closed(),
    ]
    assert resolve_original(100, events, older) == (42, RESOLVED)


def test_a_duplicate_with_no_reference_at_all_is_unresolved():
    assert resolve_original(100, [comment(5, duplicate=True), closed()], older) == (
        None,
        UNRESOLVED,
    )
    assert resolve_original(100, [closed()], older) == (None, UNRESOLVED)


def test_cross_references_alone_do_not_resolve_a_duplicate():
    events = [TimelineEvent(event="cross-referenced", created_at=CLOSE, refs=[42]), closed()]
    assert resolve_original(100, events, older) == (None, UNRESOLVED)


def test_outcome_keeps_only_labels_of_the_taxonomy():
    issue = make_issue(100, "t", labels=["bug", "topic:gui", "topic:rare", "platform:web"])
    outcome = outcome_for(issue, taxonomy(), T0, None, older)
    assert outcome.labels == ["platform:web", "topic:gui"]
    assert not outcome.duplicate and outcome.original_status is None


def test_outcome_of_a_duplicate_without_a_timeline_waits():
    issue = make_issue(100, "t", state="closed", state_reason="duplicate")
    outcome = outcome_for(issue, taxonomy(), T0, None, older)
    assert outcome.duplicate and outcome.original_status == NOT_FETCHED


def test_outcome_of_a_duplicate_with_a_timeline_names_the_original():
    issue = make_issue(100, "t", state="closed", state_reason="duplicate")
    outcome = outcome_for(
        issue, taxonomy(), T0, [comment(5, refs=[42], duplicate=True), closed()], older
    )
    assert (outcome.original, outcome.original_status) == (42, RESOLVED)


def test_an_open_issue_has_no_close_reason_in_its_outcome():
    issue = make_issue(100, "t", state="open", state_reason="reopened")
    assert outcome_for(issue, taxonomy(), T0, None, older).state_reason is None


def test_collect_uses_only_issues_the_store_knows_as_originals(store):
    store.add_issues(
        [
            make_issue(42, "original", day=1),
            make_issue(100, "copy", day=5, state="closed", state_reason="duplicate"),
            make_issue(
                101, "copy of something unknown", day=6, state="closed", state_reason="duplicate"
            ),
        ]
    )
    store.set_timeline(100, [comment(5, refs=[42], duplicate=True), closed()])
    store.set_timeline(101, [comment(5, refs=[77], duplicate=True), closed()])
    outcomes = {o.number: o for o in collect(store, taxonomy(), store.issues())}
    assert outcomes[100].original == 42
    assert outcomes[101].original_status == UNRESOLVED
    assert outcomes[42].duplicate is False


def test_fetch_timelines_skips_stored_ones_and_stops_at_the_quota(store):
    store.set_timeline(1, [])

    class Client:
        remaining = {"core": 0}
        asked: list[int] = []

        def timeline(self, number):
            self.asked.append(number)
            if number == 3:
                raise RateLimited("quota used up")
            return [closed()]

    client = Client()
    assert fetch_timelines(client, store, [1, 2, 3, 4]) == (1, 2)
    assert client.asked == [2, 3]
    assert store.timeline_numbers() == {1, 2}


def test_outcome_log_appends_once_and_settles_a_waiting_duplicate(tmp_path):
    log = OutcomeLog(tmp_path / "outcomes.jsonl")
    waiting = Outcome(
        number=100, created_at=T0, read_at=T0, duplicate=True, original_status=NOT_FETCHED
    )
    plain = Outcome(number=5, created_at=T0, read_at=T0, labels=["topic:gui"])
    assert log.append([waiting, plain]) == 2
    assert log.append([waiting, plain]) == 0
    settled = waiting.model_copy(update={"original": 42, "original_status": RESOLVED})
    changed = plain.model_copy(update={"labels": ["topic:editor"]})
    assert log.append([settled, changed]) == 1
    read = log.read()
    assert read[100].original == 42
    assert read[5].labels == ["topic:gui"]
    assert len((tmp_path / "outcomes.jsonl").read_text().splitlines()) == 3


# -- the gate ---------------------------------------------------------------------


def run_gate(labels=(), duplicate=None, confidence=None, allowed=frozenset({40, 41, 42}), **kwargs):
    picks = [LabelPick(name=name, confidence=c) for name, c in labels]
    return gate(
        picks, duplicate, confidence,
        taxonomy=taxonomy(), issue_number=100,
        allowed_duplicates=set(allowed) if allowed is not None else None,
        is_older=lambda n: n < 100, primary_group="area", source="model", **kwargs,
    )  # fmt: skip


def test_a_confident_answer_needs_no_person():
    proposal = run_gate([("topic:gui", "high"), ("platform:web", "high")])
    assert proposal.names() == ["platform:web", "topic:gui"]
    assert not proposal.needs_person and proposal.gate == []


def test_a_label_outside_the_taxonomy_is_dropped_and_a_person_looks():
    proposal = run_gate([("topic:gui", "high"), ("wontfix", "high"), ("topic:made-up", "high")])
    assert proposal.names() == ["topic:gui"]
    assert proposal.gate == [LABEL_NOT_IN_TAXONOMY]
    assert GATE_CHANGED_ANSWER in proposal.person


def test_a_repeated_label_is_kept_once_at_its_highest_confidence():
    proposal = run_gate([("topic:gui", "medium"), ("topic:gui", "high")])
    assert [(p.name, p.confidence) for p in proposal.labels] == [("topic:gui", "high")]
    assert not proposal.needs_person


def test_more_than_three_labels_of_one_kind_are_cut_to_the_surest():
    labels = [
        ("topic:gui", "medium"),
        ("topic:editor", "high"),
        ("topic:physics", "high"),
        ("topic:audio", "high"),
    ]
    proposal = run_gate(labels)
    assert proposal.names() == ["topic:audio", "topic:editor", "topic:physics"]
    assert TOO_MANY_LABELS in proposal.gate


def test_no_confident_label_of_the_primary_group_goes_to_a_person():
    assert "no_confident_area" in run_gate([("platform:web", "high")]).person
    assert "no_confident_area" in run_gate([]).person
    assert "no_confident_area" in run_gate([("topic:gui", "medium")]).person


def test_an_unsure_label_goes_to_a_person_and_a_low_one_is_not_proposed():
    proposal = run_gate([("topic:gui", "high"), ("topic:editor", "medium"), ("topic:audio", "low")])
    assert UNCERTAIN_LABEL in proposal.person
    assert proposal.names() == ["topic:editor", "topic:gui"]
    assert proposal.names("high") == ["topic:gui"]


def test_a_duplicate_must_be_one_of_the_retrieved_candidates():
    proposal = run_gate([("topic:gui", "high")], duplicate=7, confidence="high")
    assert proposal.duplicate_of is None and proposal.duplicate_confidence is None
    assert proposal.gate == [DUPLICATE_NOT_A_CANDIDATE]
    assert GATE_CHANGED_ANSWER in proposal.person


@pytest.mark.parametrize("number", [100, 150])
def test_a_duplicate_must_be_older_than_the_issue(number):
    proposal = run_gate(
        [("topic:gui", "high")], duplicate=number, confidence="high", allowed={number}
    )
    assert proposal.duplicate_of is None
    assert proposal.gate == [DUPLICATE_NOT_OLDER]


def test_a_sure_duplicate_among_the_candidates_passes():
    proposal = run_gate([("topic:gui", "high")], duplicate=42, confidence="high")
    assert (proposal.duplicate_of, proposal.duplicate_confidence) == (42, "high")
    assert not proposal.needs_person


def test_an_unsure_duplicate_goes_to_a_person():
    proposal = run_gate([("topic:gui", "high")], duplicate=42, confidence="medium")
    assert proposal.duplicate_of == 42
    assert POSSIBLE_DUPLICATE in proposal.person


def test_a_guessed_duplicate_is_not_a_proposal():
    proposal = run_gate([("topic:gui", "high")], duplicate=42, confidence="low")
    assert proposal.duplicate_of is None and proposal.duplicate_confidence is None
    assert not proposal.needs_person and proposal.gate == []


def test_an_answer_from_memory_only_has_to_exist_and_be_older():
    assert run_gate([], duplicate=7, confidence="high", allowed=None).duplicate_of == 7
    assert run_gate([], duplicate=700, confidence="high", allowed=None).duplicate_of is None


def test_a_missing_model_answer_is_a_reason_for_a_person():
    proposal = run_gate([("topic:gui", "high")], model_unavailable=True)
    assert MODEL_UNAVAILABLE in proposal.person


def test_read_at_is_kept_with_timezone(tmp_path):
    log = OutcomeLog(tmp_path / "o.jsonl")
    moment = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
    log.append([Outcome(number=1, created_at=T0, read_at=moment)])
    assert log.read()[1].read_at == moment
