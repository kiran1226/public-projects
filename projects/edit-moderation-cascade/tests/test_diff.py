"""What an edit changed: numbered lines, clipped and cut, fetched fifty revisions at a time."""

from __future__ import annotations

import pytest

from editwatch.diff import compute_change, fetch_changes, mark_difference, mark_pairs

from conftest import make_edit


def test_removed_and_added_lines_are_numbered_from_one_and_marked():
    old = "The bridge opened in 1901.\nIt carries a road.\nIt was repainted twice."
    new = "The bridge opened in 1910.\nIt carries a road.\nIt was repainted twice.\nIt is closed on Sundays."
    change = compute_change(5, old, new)
    assert change.lines == [
        "1 - The bridge opened in ⟦1901.⟧",
        "2 + The bridge opened in ⟦1910.⟧",
        "3 + It is closed on Sundays.",
    ]
    assert change.text == "\n".join(change.lines)
    assert (change.rev_id, change.available, change.truncated) == (5, True, False)


def test_unchanged_and_blank_lines_are_left_out():
    old = "first\n\nsecond\nthird"
    new = "first\n\nsecond\n\n   \nfourth\nthird\n"
    assert compute_change(5, old, new).lines == ["1 + fourth"]


def test_a_removal_shows_only_what_went_and_identical_texts_show_nothing():
    assert compute_change(5, "one\ntwo\nthree", "one\nthree").lines == ["1 - two"]
    same = compute_change(5, "one\ntwo", "one\ntwo")
    assert (same.lines, same.truncated, same.available) == ([], False, True)


def test_a_rewritten_block_lists_what_went_before_what_came():
    assert compute_change(5, "a\nb\nc", "x\ny").lines == [
        "1 - a",
        "2 - b",
        "3 - c",
        "4 + x",
        "5 + y",
    ]


def test_a_very_long_changed_line_is_clipped_around_the_difference():
    before = "The lighthouse stands on a rock. " * 40
    after = "Visitors may climb the stairs. " * 40
    old = f"{before}It was built in 1850 by the harbour board. {after}"
    new = old.replace("1850", "1950")
    removed, added = compute_change(5, f"intro\n{old}\noutro", f"intro\n{new}\noutro").lines
    assert removed.startswith("1 - … ") and removed.endswith(" …")
    assert added.startswith("2 + … ") and added.endswith(" …")
    assert "built in ⟦1850⟧ by" in removed and "built in ⟦1950⟧ by" in added
    assert len(removed) < 300 and len(added) < 300 < len(old)


def test_a_change_is_cut_at_the_character_limit_and_says_so():
    new = "\n".join(f"added line number {n:03d} with some words in it" for n in range(100))
    change = compute_change(5, "", new, limit=300)
    assert change.truncated is True
    assert len(change.text) <= 300
    assert change.lines == [
        f"{n + 1} + added line number {n:03d} with some words in it" for n in range(6)
    ]
    assert compute_change(5, "", new, limit=100_000).truncated is False


def test_a_first_line_longer_than_the_limit_is_cut_to_it():
    change = compute_change(5, "", "w" * 700, limit=200)
    assert change.truncated is True
    assert change.lines == ["1 + " + "w" * 196]


@pytest.mark.parametrize(("old", "new"), [(None, "text"), ("text", None), (None, None)])
def test_a_change_is_unavailable_when_a_text_is_missing(old, new):
    change = compute_change(5, old, new)
    assert (change.available, change.lines, change.rev_id) == (False, [], 5)


def test_an_edit_with_no_parent_shows_only_added_lines():
    assert compute_change(5, "", "A new article.\n\nWith two lines.").lines == [
        "1 + A new article.",
        "2 + With two lines.",
    ]


# -- fetching -------------------------------------------------------------------------


def page_edits(count: int, first: int = 1000):
    """Edits to different pages, each with its own parent, and the text of every revision."""
    edits, texts = [], {}
    for n in range(count):
        rev_id = first + 2 * n
        edits.append(make_edit(rev_id, parent_id=rev_id - 1, minute=n))
        texts[rev_id - 1] = "An opening line.\nA second line."
        texts[rev_id] = f"An opening line.\nA second line, changed in edit {n}."
    return edits, texts


def test_changes_are_fetched_two_revisions_an_edit_and_at_most_fifty_a_request(fake, client, store):
    edits, fake.texts = page_edits(60)
    said = []
    assert fetch_changes(client, store, edits, progress=said.append) == 60
    assert [len(ids) for ids in fake.revids_asked()] == [50, 50, 20]
    asked = [rev_id for ids in fake.revids_asked() for rev_id in ids]
    assert sorted(asked) == sorted(fake.texts)
    assert store.change_ids() == {e.rev_id for e in edits}
    assert store.change(1002).lines == [
        "1 - A second ⟦line.⟧",
        "2 + A second ⟦line, changed in edit 1.⟧",
    ]
    assert said == [
        "25 of 60 changes fetched",
        "50 of 60 changes fetched",
        "60 of 60 changes fetched",
    ]


def test_a_change_the_store_has_is_not_fetched_again(fake, client, store):
    edits, fake.texts = page_edits(10)
    assert fetch_changes(client, store, edits[:8]) == 8
    assert len(fake.requests) == 1
    assert fetch_changes(client, store, edits) == 2
    assert fake.revids_asked()[1] == [1015, 1016, 1017, 1018]
    assert fetch_changes(client, store, edits) == 0
    assert len(fake.requests) == 2


def test_an_edit_without_a_parent_is_fetched_alone_and_shows_only_added_lines(fake, client, store):
    fake.texts = {40: "A new article.\nWith two lines."}
    fetch_changes(client, store, [make_edit(40, parent_id=0)])
    assert fake.revids_asked() == [[40]]
    assert store.change(40).lines == ["1 + A new article.", "2 + With two lines."]


@pytest.mark.parametrize("gone", [20, 19])
def test_a_revision_that_cannot_be_read_is_stored_as_unavailable_and_not_asked_for_again(
    fake, client, store, gone
):
    fake.texts = {19: "before", 20: "after"}
    fake.hidden = {gone}
    assert fetch_changes(client, store, [make_edit(20, parent_id=19)]) == 1
    assert store.change(20).available is False
    assert fetch_changes(client, store, [make_edit(20, parent_id=19)]) == 0
    assert len(fake.requests) == 1


def test_fetched_changes_are_cut_at_the_limit_they_are_given(fake, client, store):
    fake.texts = {19: "", 20: "\n".join(f"a pasted line, number {n}" for n in range(200))}
    fetch_changes(client, store, [make_edit(20, parent_id=19)], limit=250)
    change = store.change(20)
    assert change.truncated is True
    assert len(change.text) <= 250


def test_the_differing_part_of_a_changed_line_is_marked_in_both_versions():
    old = "Its call carries far over open water and it smells like old socks. Males sing."
    new = "Its call carries far over open water. Males sing."
    assert mark_difference(old, new) == (
        "Its call carries far over open ⟦water and it smells like old socks.⟧ Males sing.",
        "Its call carries far over open ⟦water.⟧ Males sing.",
    )


def test_marks_cover_whole_words():
    assert mark_difference("opened in 1887", "opened in 1987") == (
        "opened in ⟦1887⟧",
        "opened in ⟦1987⟧",
    )
    assert mark_difference("a b c", "a b X c") == ("a b ⟦c⟧", "a b ⟦X c⟧")


def test_hand_written_changes_get_the_same_marks_once():
    lines = ["1 - The bridge opened in 1901.", "2 + The bridge opened in 1910."]
    marked = mark_pairs(lines)
    assert marked == ["1 - The bridge opened in ⟦1901.⟧", "2 + The bridge opened in ⟦1910.⟧"]
    assert mark_pairs(marked) == marked


def test_a_run_of_removed_lines_is_not_paired_with_one_added_line():
    lines = ["1 - first old line", "2 - second old line", "3 + one new line"]
    assert mark_pairs(lines) == lines
