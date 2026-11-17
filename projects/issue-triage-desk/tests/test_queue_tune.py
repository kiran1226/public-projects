"""The queue (a proposal joined with its issue) and the tables settings are chosen from."""

from __future__ import annotations

from datetime import UTC, datetime

from triagedesk.duplicates import Families
from triagedesk.models import LabelPick, Outcome, Prediction, Proposal
from triagedesk.queue import build_item, build_queue, format_item
from triagedesk.truth import RESOLVED
from triagedesk.tune import retrieval_table, similarity_grid

from conftest import make_desk, make_issue, world_issues

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def prediction(number, labels=(("topic:gui", "high"),), duplicate=None, candidates=(3, 6, 9, 12),
               config="model", person=(), created=None) -> Prediction:  # fmt: skip
    return Prediction(
        number=number,
        created_at=created or NOW,
        written_at=NOW,
        system_version="v1",
        config=config,
        proposal=Proposal(
            labels=[LabelPick(name=n, confidence=c) for n, c in labels],
            duplicate_of=duplicate,
            duplicate_confidence="high" if duplicate is not None else None,
            person=list(person),
            source="model",
        ),
        candidates=list(candidates),
    )


def store_with(tmp_path, *extra):
    desk = make_desk(tmp_path, issues=world_issues() + list(extra))
    return desk


def test_an_item_joins_the_row_with_the_stored_issue_and_three_earlier_ones(tmp_path):
    desk = store_with(
        tmp_path, make_issue(500, "Popup menu loses focus", "The popup menu loses focus.", day=110)
    )
    desk.store.set_note(500, "v1", "About focus in a popup.")
    item = build_item(desk.store, prediction(500))
    assert item.issue.title == "Popup menu loses focus"
    assert [e.issue.number for e in item.earlier] == [3, 6, 9]
    assert item.note == "About focus in a popup."
    assert not any(e.proposed for e in item.earlier)


def test_the_proposed_duplicate_is_shown_first_even_if_it_ranked_low(tmp_path):
    desk = store_with(tmp_path, make_issue(500, "Popup menu loses focus", "text", day=110))
    item = build_item(desk.store, prediction(500, duplicate=12))
    assert [e.issue.number for e in item.earlier] == [12, 3, 6]
    assert item.earlier[0].proposed
    assert "#12" in item.plan.comment and item.plan.add_labels == ["topic:gui"]


def test_an_issue_missing_from_the_store_gives_no_item(tmp_path):
    desk = store_with(tmp_path)
    assert build_item(desk.store, prediction(999)) is None
    assert build_queue(desk.store, [prediction(999)]) == []


def test_the_queue_is_newest_first_filtered_by_configuration_and_limited(tmp_path):
    arrivals = [make_issue(500 + n, f"Popup bug {n}", "text", day=110 + n) for n in range(4)]
    desk = store_with(tmp_path, *arrivals)
    rows = [prediction(i.number, created=i.created_at) for i in arrivals]
    rows += [prediction(i.number, created=i.created_at, config="rules") for i in arrivals]
    queue = build_queue(desk.store, rows, config="model", limit=3)
    assert [item.issue.number for item in queue] == [503, 502, 501]
    assert all(item.prediction.config == "model" for item in queue)


def test_format_item_shows_the_answer_the_route_and_the_outcome(tmp_path):
    desk = store_with(tmp_path, make_issue(500, "Popup menu loses focus", "text", day=110))
    outcome = Outcome(number=500, created_at=NOW, read_at=NOW, duplicate=True, original=12,
                      original_status=RESOLVED)  # fmt: skip
    row = prediction(
        500, labels=(("topic:gui", "medium"),), duplicate=12, person=("uncertain_label",)
    )
    text = format_item(build_item(desk.store, row, outcome))
    assert "#500  Popup menu loses focus" in text
    assert "topic:gui (medium)" in text and "duplicate: #12 (high)" in text
    assert "to a person: uncertain_label" in text
    assert "closed as a duplicate of #12" in text


def test_format_item_says_handled_alone_and_none_proposed(tmp_path):
    desk = store_with(tmp_path, make_issue(500, "Popup menu loses focus", "text", day=110))
    text = format_item(build_item(desk.store, prediction(500)))
    assert "duplicate: none proposed" in text and "handled alone" in text


def duplicate_world(tmp_path):
    copy = make_issue(
        500,
        "Reverb bus pitch breaks",
        "### Issue description\nThe pitch breaks when the reverb changes and the microphone is reset.",
        day=110,
    )
    other = make_issue(
        501, "Completely unrelated export crash", "### Issue description\nExport crashes.", day=111
    )
    desk = store_with(tmp_path, copy, other)
    original = next(
        i for i in world_issues() if "pitch" in i.title.lower() or "Pitch" in i.title
    ).number
    outcomes = {
        500: Outcome(number=500, created_at=copy.created_at, read_at=NOW, duplicate=True,
                     original=original, original_status=RESOLVED),
        501: Outcome(number=501, created_at=other.created_at, read_at=NOW),
    }  # fmt: skip
    return desk, [copy, other], outcomes


def test_the_retrieval_table_counts_duplicates_whose_original_is_found(tmp_path):
    desk, issues, outcomes = duplicate_world(tmp_path)
    table = retrieval_table(desk.index, issues, outcomes, Families(), limit=20)
    assert table["duplicates"] == 1
    assert set(table["modes"]) == {"fused", "vector", "words"}
    for hits in table["modes"].values():
        assert hits[1] <= hits[3] <= hits[5] <= hits[20] <= 1


def test_a_higher_similarity_threshold_never_flags_more(tmp_path):
    desk, issues, outcomes = duplicate_world(tmp_path)
    rows = similarity_grid(desk.index, issues, outcomes, Families(), "fused")
    flagged = [row["flagged"] for row in rows]
    assert flagged == sorted(flagged, reverse=True)
    assert all(row["right"] + row["new_issues"] <= row["flagged"] for row in rows)
