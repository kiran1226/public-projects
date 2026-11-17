"""The local store and the append-only prediction log."""

from __future__ import annotations

import gzip
import json
import re
from datetime import date, timedelta

import numpy as np
import pytest

from triagedesk.embed import HashEmbedder
from triagedesk.log import PredictionLog, from_row, system_version, to_row
from triagedesk.models import LabelPick, Prediction, Proposal, TimelineEvent
from triagedesk.text import words

from conftest import REPO, T0, make_issue


def day(offset: float):
    return T0 + timedelta(days=offset)


# -- store ---------------------------------------------------------------------------


def test_a_refresh_updates_labels_and_state_but_never_title_or_body(store):
    store.add_issues([make_issue(7, "Tilemap flickers", "Tiles shimmer while the camera pans.")])
    store.add_issues(
        [
            make_issue(
                7,
                "Edited title about gamepads",
                "Edited body.",
                labels=["topic:2d", "bug"],
                state="closed",
                state_reason="completed",
                closed_day=3,
            )
        ]  # fmt: skip
    )
    stored = store.issue(7)
    assert (stored.title, stored.body) == (
        "Tilemap flickers",
        "Tiles shimmer while the camera pans.",
    )
    assert stored.labels == ["bug", "topic:2d"]
    assert (stored.state, stored.state_reason) == ("closed", "completed")
    assert stored.closed_at == day(3)
    assert store.search_words('"tilemap"', day(9), 5)[0][0] == 7
    assert store.search_words('"gamepads"', day(9), 5) == []


def test_first_seen_keeps_the_labels_and_state_of_the_first_sighting(store):
    store.add_issues([make_issue(7, "Tilemap flickers", labels=["bug"])], seen_at=day(2))
    store.add_issues(
        [
            make_issue(
                7, "Tilemap flickers", labels=["bug", "topic:2d"], state="closed", closed_day=5
            )
        ],
        seen_at=day(20),
    )
    assert store.first_seen(7) == (day(2), ["bug"], "open")
    assert store.refreshed_at(7) == day(20)
    assert store.first_seen(8) is None


def test_add_issues_returns_only_the_number_of_new_issues(store):
    first = [make_issue(n, f"Report {n}", day=n) for n in (1, 2, 3)]
    assert store.add_issues(first) == 3
    assert store.add_issues([*first[:2], make_issue(4, "Report 4", day=4)]) == 1
    assert store.add_issues(first) == 0
    assert store.count() == 4


def test_issues_are_read_by_a_half_open_window_oldest_first(store):
    store.add_issues(
        [
            make_issue(40, "Opened on day three", day=3),
            make_issue(12, "Opened on day one, higher number", day=1),
            make_issue(11, "Opened on day one", day=1),
            make_issue(20, "Opened on day two", day=2),
            make_issue(5, "Opened on day zero", day=0),
        ]
    )
    assert [i.number for i in store.issues()] == [5, 11, 12, 20, 40]
    assert [i.number for i in store.issues(day(1), day(3))] == [11, 12, 20]
    assert [i.number for i in store.issues(start=day(2))] == [20, 40]
    assert [i.number for i in store.issues(end=day(1))] == [5]


def test_span_count_and_numbers_describe_what_is_stored(store):
    assert (store.span(), store.count(), store.numbers()) == ((None, None), 0, set())
    assert store.issue(1) is None
    store.add_issues([make_issue(n, f"Report {n}", day=n) for n in (4, 2, 9)])
    assert store.span() == (day(2), day(9))
    assert store.count() == 3
    assert store.numbers() == {2, 4, 9}


def stored_out_of_order(store) -> np.ndarray:
    """Three issues whose numbers do not follow their creation order, and a vector for each."""
    store.add_issues(
        [
            make_issue(1, "Audio stutters", day=2),
            make_issue(2, "Gamepad ignored", day=3),
            make_issue(3, "Shader crash", day=1),
        ]
    )
    return HashEmbedder().embed(["audio stutters", "gamepad ignored", "shader crash"])


def test_vectors_come_back_unchanged_in_creation_order(store):
    vectors = stored_out_of_order(store)
    store.add_vectors("hash-64", [1, 2, 3], vectors)
    numbers, times, matrix = store.vectors("hash-64")
    assert numbers.tolist() == [3, 1, 2]
    assert times.tolist() == [day(1).timestamp(), day(2).timestamp(), day(3).timestamp()]
    np.testing.assert_array_equal(matrix, vectors[[2, 0, 1]])
    assert len(store.vectors("another-model")[0]) == 0


def test_missing_vectors_shrinks_as_vectors_are_added(store):
    vectors = stored_out_of_order(store)
    assert store.missing_vectors("hash-64") == [3, 1, 2]
    store.add_vectors("hash-64", [1], vectors[:1])
    assert store.missing_vectors("hash-64") == [3, 2]
    store.add_vectors("hash-64", [2, 3], vectors[1:])
    assert store.missing_vectors("hash-64") == []


def test_a_timeline_comes_back_as_stored_and_an_unknown_one_is_none(store):
    events = [
        TimelineEvent(event="labeled", created_at=day(1), actor_type="User", label="topic:2d"),
        TimelineEvent(event="commented", created_at=day(2), actor_type="Bot", refs=[5],
                      outside_refs=["example/proposals#3"], says_duplicate=True),
        TimelineEvent(event="renamed", created_at=day(3), old_title="Tilemap flickers"),
        TimelineEvent(event="closed", created_at=day(4), state_reason="duplicate"),
    ]  # fmt: skip
    store.set_timeline(7, events)
    store.set_timeline(9, [])
    assert store.timeline(7) == events
    assert store.timeline(9) == []
    assert store.timeline(8) is None, "an issue whose timeline was never read"
    assert store.timeline_numbers() == {7, 9}


def test_notes_are_kept_per_system_version_and_the_latest_is_the_default(store):
    store.set_note(7, "v1", "Driver problem, not ours.")
    store.set_note(7, "v2", "Same report as an earlier one.")
    assert store.note(7, "v1") == "Driver problem, not ours."
    assert store.note(7) == "Same report as an earlier one."
    assert store.note(7, "v3") is None
    assert store.note(8) is None


def test_state_values_are_set_read_and_replaced(store):
    assert store.get_state("fetched:2026-01") is None
    store.set_state("fetched:2026-01", "partial")
    store.set_state("fetched:2026-01", "done")
    assert store.get_state("fetched:2026-01") == "done"


# -- log -----------------------------------------------------------------------------


def prediction(number: int = 7, *, config: str = "classifier", version: str = "0a1b2c3d4e",
               proposal: Proposal | None = None, **fields) -> Prediction:  # fmt: skip
    return Prediction(
        number=number,
        created_at=T0,
        written_at=T0 + timedelta(hours=1),
        system_version=version,
        config=config,
        proposal=proposal or Proposal(),
        **fields,
    )


FULL = prediction(
    proposal=Proposal(
        labels=[
            LabelPick(name="topic:rendering", confidence="high"),
            LabelPick(name="platform:linux", confidence="medium"),
            LabelPick(name="topic:audio", confidence="low"),
        ],
        duplicate_of=3,
        duplicate_confidence="medium",
        person=["possible_duplicate", "uncertain_label"],
        gate=["TOO_MANY_LABELS"],
        source="model",
    ),
    candidates=[3, 2, 1],
    cost_usd=0.001234,
    latency_ms=850,
    seen_labels=True,
    seen_closed=True,
)


def test_a_full_prediction_survives_the_row_form():
    row = json.loads(json.dumps(to_row(FULL)))
    assert from_row(row) == FULL


def test_a_minimal_prediction_writes_only_the_required_keys_and_survives():
    minimal = prediction()
    row = to_row(minimal)
    assert set(row) == {"n", "c", "w", "v", "cfg", "src", "l", "cand"}
    assert from_row(json.loads(json.dumps(row))) == minimal


@pytest.mark.parametrize("name", ["log.jsonl", "log.jsonl.gz"])
def test_append_adds_a_row_once_and_nothing_on_a_repeat(tmp_path, name):
    path = tmp_path / "log" / name
    log = PredictionLog(path)
    assert log.append([FULL, prediction(8), FULL]) == 2
    assert log.append([FULL]) == 0
    reopened = PredictionLog(path)
    assert reopened.append([prediction(8), prediction(9)]) == 1
    assert PredictionLog(path).read() == [FULL, prediction(8), prediction(9)]


@pytest.mark.parametrize("changed", [{"version": "ffffffffff"}, {"config": "model"}])
def test_another_version_or_configuration_of_the_same_issue_is_a_new_row(tmp_path, changed):
    log = PredictionLog(tmp_path / "log.jsonl")
    log.append([prediction(7)])
    other = prediction(7, **changed)
    assert not log.has(*other.key)
    assert log.append([other]) == 1
    assert log.has(*other.key)
    assert [p.number for p in log.read()] == [7, 7]


def test_a_log_that_was_never_written_reads_as_empty(tmp_path):
    log = PredictionLog(tmp_path / "log.jsonl.gz")
    assert log.read() == []
    assert not log.has(7, "classifier", "0a1b2c3d4e")
    assert log.append([]) == 0
    assert not log.path.exists()


def test_a_gz_log_is_compressed_and_a_jsonl_log_is_one_row_per_line(tmp_path):
    plain, packed = tmp_path / "log.jsonl", tmp_path / "log.jsonl.gz"
    for path in (plain, packed):
        PredictionLog(path).append([FULL, prediction(8)])
    lines = plain.read_text("utf-8").splitlines()
    assert [json.loads(line)["n"] for line in lines] == [7, 8]
    assert packed.read_bytes()[:2] == b"\x1f\x8b"
    assert gzip.decompress(packed.read_bytes()).decode("utf-8") == plain.read_text("utf-8")


def strings(value):
    """Every string in a decoded JSON value, keys excluded."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def test_the_written_file_holds_numbers_and_label_names_but_no_issue_text(tmp_path):
    issue = make_issue(41, "Tilemap flickers near the camera edge", "Walls shimmer while panning.")
    written = FULL.model_copy(update={"number": issue.number, "created_at": issue.created_at})
    path = tmp_path / "log.jsonl"
    PredictionLog(path).append([written])
    raw = path.read_text("utf-8")
    assert not [word for word in words(f"{issue.title} {issue.body}") if word in raw.lower()]
    proposal = written.proposal
    allowed = {
        written.created_at.isoformat(timespec="seconds"),
        written.written_at.isoformat(timespec="seconds"),
        written.system_version,
        written.config,
        proposal.source,
        *(pick.name for pick in proposal.labels),
        *("h", "m", "l"),
        *proposal.person,
        *proposal.gate,
    }
    assert set(strings(json.loads(raw))) == allowed
    assert not {"title", "body"} & (set(Prediction.model_fields) | set(Proposal.model_fields))


PARTS = (
    {"repo": REPO, "cut": date(2026, 5, 1)},
    ["topic:audio", "topic:rendering"],
    "prompt text, third revision",
)


def test_system_version_is_stable_for_equal_input():
    same_in_another_order = ({"cut": date(2026, 5, 1), "repo": REPO}, *PARTS[1:])
    assert system_version(*PARTS) == system_version(*same_in_another_order)
    assert re.fullmatch(r"[0-9a-f]{10}", system_version(*PARTS))


@pytest.mark.parametrize("position", range(len(PARTS)))
def test_system_version_changes_when_any_part_changes(position):
    changed = list(PARTS)
    changed[position] = "something else"
    assert system_version(*changed) != system_version(*PARTS)
