"""The local store, and the backfill that fills it with slices of history."""

from __future__ import annotations

from dataclasses import replace

import pytest

from editwatch.history import backfill, slice_starts
from editwatch.models import Change
from editwatch.store import Store
from editwatch.wiki import REVERTED_TAG

from conftest import api_item, at, make_edit

# -- edits ----------------------------------------------------------------------------


def test_an_edit_is_stored_once(store):
    edit = make_edit(5, comment="first version")
    assert store.add_edits([(edit, [])], source="history") == 1
    again = make_edit(5, comment="second version")
    assert store.add_edits([(again, []), (make_edit(6), [])], source="history") == 1
    assert store.count() == 2
    assert store.edit(5).comment == "first version"


def test_a_stored_edit_reads_back_as_it_was(store):
    edit = make_edit(
        812, parent_id=790, title="Example Lighthouse", minute=90, minor=True, temp=True,
        old_len=5200, new_len=4100, comment="/* Keepers */ trimmed the list",
    )  # fmt: skip
    store.add_edits([(edit, ["mobile edit"])], source="history")
    assert store.edit(812) == edit
    assert store.tags(812) == ["mobile edit"]
    assert store.edit(813) is None
    assert store.tags(813) == []


def test_the_outcome_comes_from_the_reverted_tag_when_one_is_given(store):
    rows = [
        (make_edit(1), ["mobile edit", REVERTED_TAG]),
        (make_edit(2), ["mobile edit"]),
        (make_edit(3), []),
    ]
    store.add_edits(rows, source="history", reverted_tag=REVERTED_TAG)
    assert store.reverted() == {1: True, 2: False, 3: False}


def test_the_outcome_stays_unknown_without_a_reverted_tag_to_look_for(store):
    store.add_edits([(make_edit(1), [REVERTED_TAG]), (make_edit(2), [])], source="live")
    assert store.reverted() == {}
    assert store.count() == 2


def test_only_edits_with_an_outcome_are_in_reverted(store):
    store.add_edits([(make_edit(1), [REVERTED_TAG])], source="history", reverted_tag=REVERTED_TAG)
    store.add_edits([(make_edit(2), [REVERTED_TAG])], source="live")
    assert store.reverted() == {1: True}


def test_edits_between_two_moments_include_the_start_and_not_the_end(store):
    store.add_edits([(make_edit(n, minute=n * 10), []) for n in range(1, 6)], source="history")
    assert [e.rev_id for e in store.edits(at(20), at(40))] == [2, 3]
    assert [e.rev_id for e in store.edits(start=at(40))] == [4, 5]
    assert [e.rev_id for e in store.edits(end=at(20))] == [1]
    assert len(store.edits()) == 5


def test_edits_come_oldest_first_and_by_revision_within_a_second(store):
    rows = [
        (make_edit(9, minute=3), []),
        (make_edit(4, minute=5), []),
        (make_edit(7, minute=3), []),
        (make_edit(8, minute=1), []),
    ]
    store.add_edits(rows, source="history")
    assert [e.rev_id for e in store.edits()] == [8, 7, 9, 4]


def test_edits_by_bots_are_left_out_unless_asked_for(store):
    store.add_edits([(make_edit(1), []), (make_edit(2, bot=True), [])], source="history")
    assert [e.rev_id for e in store.edits()] == [1]
    assert [e.rev_id for e in store.edits(people_only=False)] == [1, 2]


def test_edits_and_counts_can_be_limited_to_one_source(store):
    store.add_edits([(make_edit(1), []), (make_edit(2), [])], source="history")
    store.add_edits([(make_edit(3), [])], source="live")
    assert [e.rev_id for e in store.edits(source="live")] == [3]
    assert [e.rev_id for e in store.edits(source="history")] == [1, 2]
    assert (store.count(), store.count("history"), store.count("live")) == (3, 2, 1)


# -- changes, baseline scores, state --------------------------------------------------


def test_a_change_round_trips_and_the_newest_one_wins(store):
    change = Change(rev_id=5, lines=["1 - an old line", "2 + a new line"], truncated=True)
    store.set_change(change)
    store.set_change(Change(rev_id=6, available=False))
    assert store.change(5) == change
    assert store.change(6).available is False
    assert store.change(7) is None
    assert store.change_ids() == {5, 6}
    store.set_change(Change(rev_id=5, lines=["1 + only this"]))
    assert store.change(5).lines == ["1 + only this"]


def test_baseline_scores_round_trip_with_the_ones_that_are_missing(store):
    store.set_baseline(5, 0.25)
    store.set_baseline(6, None)
    store.set_baseline(5, 0.75)
    assert store.baseline() == {5: 0.75, 6: None}


def test_state_round_trips(store):
    assert store.get_state("cursor") is None
    store.set_state("cursor", "a1")
    store.set_state("cursor", "a2")
    assert store.get_state("cursor") == "a2"


def test_a_store_on_disk_keeps_what_it_was_given(tmp_path):
    path = tmp_path / "nested" / "editwatch.db"
    first = Store(path)
    first.add_edits([(make_edit(5), [])], source="history")
    first.set_state("cursor", "a1")
    first.close()
    second = Store(path)
    assert second.edit(5) == make_edit(5)
    assert second.get_state("cursor") == "a1"


# -- history --------------------------------------------------------------------------


def test_slices_start_every_few_hours_up_to_but_not_at_the_end():
    assert list(slice_starts(at(0), at(360), 2)) == [at(0), at(120), at(240)]
    assert list(slice_starts(at(0), at(361), 2)) == [at(0), at(120), at(240), at(360)]
    assert list(slice_starts(at(0), at(0), 2)) == []


@pytest.fixture
def history(fake):
    """Seven hours of edits, one every twenty minutes. Every fifth one was reverted."""
    fake.changes = [
        api_item(make_edit(100 + n, minute=20 * n), tags=[REVERTED_TAG] if n % 5 == 0 else [])
        for n in range(22)
    ]
    return fake


def test_backfill_fetches_each_slice_once_and_stores_edits_with_their_outcome(
    history, client, store, settings
):
    settings = replace(settings, slice_hours=2, slice_edits=3)
    said = []
    counts = backfill(client, store, settings, start=at(0), end=at(360), progress=said.append)
    assert counts == {"fetched": 9, "new": 9, "skipped": 0}
    assert [p["rcstart"] for p in history.asked("recentchanges")] == [
        "2026-11-01T00:00:00Z",
        "2026-11-01T02:00:00Z",
        "2026-11-01T04:00:00Z",
    ]
    assert {p["rclimit"] for p in history.asked("recentchanges")} == {"3"}
    assert [e.rev_id for e in store.edits(source="history")] == [
        100,
        101,
        102,
        106,
        107,
        108,
        112,
        113,
        114,
    ]
    assert store.reverted()[100] is True and store.reverted()[101] is False
    assert len(said) == 3 and "3 edits in 40.0 minutes" in said[0]


def test_backfill_skips_slices_it_already_fetched(history, client, store, settings):
    settings = replace(settings, slice_hours=2, slice_edits=3)
    backfill(client, store, settings, start=at(0), end=at(240))
    assert len(history.requests) == 2
    counts = backfill(client, store, settings, start=at(0), end=at(360))
    assert counts == {"fetched": 3, "new": 3, "skipped": 2}
    starts = [p["rcstart"] for p in history.asked("recentchanges")]
    assert len(starts) == len(set(starts)) == 3
    assert backfill(client, store, settings, start=at(0), end=at(360)) == {
        "fetched": 0,
        "new": 0,
        "skipped": 3,
    }
    assert len(history.requests) == 3


def test_backfill_remembers_a_slice_that_held_no_edits(fake, client, store, settings):
    settings = replace(settings, slice_hours=2)
    assert backfill(client, store, settings, start=at(0), end=at(240)) == {
        "fetched": 0,
        "new": 0,
        "skipped": 0,
    }
    assert backfill(client, store, settings, start=at(0), end=at(240))["skipped"] == 2
    assert len(fake.requests) == 2


def test_backfill_never_stores_an_edit_made_at_or_after_the_end(history, client, store, settings):
    settings = replace(settings, slice_hours=2, slice_edits=10)
    backfill(client, store, settings, start=at(0), end=at(360))
    stored = store.edits(people_only=False)
    assert max(e.timestamp for e in stored) == at(340)
    assert store.edits(start=at(360), people_only=False) == []
    assert store.edit(118) is None  # made exactly at the end of the window


def test_backfill_counts_an_edit_in_two_slices_as_fetched_twice_and_new_once(
    history, client, store, settings
):
    settings = replace(settings, slice_hours=2, slice_edits=10)
    counts = backfill(client, store, settings, start=at(0), end=at(360))
    assert counts == {"fetched": 26, "new": 18, "skipped": 0}
    assert store.count() == 18
