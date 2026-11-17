"""The live stream and its recordings: parsing, reconnecting, recording and playing back."""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest

from editwatch.models import Edit
from editwatch.recorder import arrival_offsets, load, record, replay
from editwatch.stream import StreamReader, parse_event, parse_sse
from editwatch.wiki import FeedError

from conftest import ACCOUNT, FakeClock, at, make_edit, sse_lines, stream_event


def event(rev_id: int, **extra) -> dict:
    return stream_event(make_edit(rev_id, minute=rev_id), **extra)


class Connections:
    """A scripted stream. Each connection is a list of lines; an exception in it is raised there."""

    def __init__(self, *connections):
        self.connections = list(connections)
        self.opened_with: list[str | None] = []

    def __call__(self, last_event_id):
        self.opened_with.append(last_event_id)
        script = self.connections.pop(0)

        def lines():
            for line in script:
                if isinstance(line, Exception):
                    raise line
                yield line

        return lines()


# -- server-sent events ---------------------------------------------------------------


def test_sse_events_come_with_their_ids():
    lines = ["event: message", "id: 41", 'data: {"a": 1}', "", "id: 42", 'data: {"a": 2}', ""]
    assert list(parse_sse(lines)) == [("41", '{"a": 1}'), ("42", '{"a": 2}')]


def test_sse_data_on_several_lines_is_one_event():
    lines = ["id: 7", "data: first", "data:second", "data: ", "data: fourth", ""]
    assert list(parse_sse(lines)) == [("7", "first\nsecond\n\nfourth")]


def test_sse_comments_and_keep_alives_are_not_events():
    lines = [
        ":ok",
        "",
        ": keep-alive",
        "",
        "",
        "id: 1",
        ": a comment inside an event",
        "data: x",
        "",
    ]
    assert list(parse_sse(lines)) == [("1", "x")]


def test_a_final_sse_event_without_a_blank_line_is_still_read():
    assert list(parse_sse(["id: 1", "data: x", "", "id: 2", "data: y"])) == [("1", "x"), ("2", "y")]


# -- one event ------------------------------------------------------------------------


def test_an_article_edit_on_the_watched_wiki_is_kept(settings):
    edit = make_edit(
        812, parent_id=790, title="Example Lighthouse", minute=90, minor=True, old_len=5200,
        new_len=4100, comment="/* Keepers */ trimmed the list",
    )  # fmt: skip
    parsed = parse_event(stream_event(edit), settings)
    assert parsed == edit.model_copy(update={"event_time": at(90, seconds=2)})
    assert parsed.timestamp == at(90)


@pytest.mark.parametrize(
    "change",
    [
        {"wiki": "dewiki"},
        {"namespace": 1},
        {"bot": True},
        {"type": "new"},
        {"type": "log"},
        {"type": "categorize"},
    ],
)
def test_anything_but_an_article_edit_by_a_person_on_this_wiki_is_dropped(settings, change):
    assert parse_event(event(5, **change), settings) is None


def test_a_temporary_account_is_read_from_the_name_and_the_name_is_not_kept(settings):
    temporary = parse_event(event(5, user="~2026-12345-6"), settings)
    registered = parse_event(event(6, user=ACCOUNT), settings)
    assert temporary.temp is True
    assert registered.temp is False
    assert "~2026-12345-6" not in temporary.model_dump_json()
    assert ACCOUNT not in registered.model_dump_json()


def test_a_name_in_the_summary_of_a_stream_event_is_scrubbed(settings):
    parsed = parse_event(event(5, comment=f"Reverted 1 edit by {ACCOUNT} (talk)"), settings)
    assert parsed.comment == "Reverted 1 edit by [name]"


def test_the_event_time_is_read_and_may_be_missing(settings):
    payload = event(5)
    payload["meta"]["dt"] = "2026-11-01T00:05:09Z"
    assert parse_event(payload, settings).event_time == at(5, seconds=9)
    del payload["meta"]
    assert parse_event(payload, settings).event_time is None


@pytest.mark.parametrize(
    "broken",
    [
        {"revision": None},
        {"revision": {"old": 4}},
        {"title": None},
        {"timestamp": "2026-11-01T00:05:00Z"},
    ],
)
def test_a_stream_event_in_a_changed_shape_fails_loudly(settings, broken):
    with pytest.raises(FeedError, match="no longer parses"):
        parse_event(event(5, **broken), settings)


@pytest.mark.parametrize(
    "broken", [{"length": "long"}, {"meta": "2026-11-01T00:05:02Z"}, {"user": 42}]
)
def test_a_stream_event_with_a_field_of_the_wrong_type_fails_loudly(settings, broken):
    with pytest.raises(FeedError, match="no longer parses"):
        parse_event(event(5, **broken), settings)


# -- the reader -----------------------------------------------------------------------


def test_the_reader_resumes_after_the_last_event_when_the_connection_ends(settings):
    stream = Connections(
        sse_lines([("a1", event(1)), ("a2", event(2))]),
        sse_lines([("a3", event(3))]),
    )
    reader = StreamReader(settings, opener=stream, sleep=FakeClock().sleep)
    edits = list(reader.edits(stop=lambda: reader.kept >= 3))
    assert [e.rev_id for e in edits] == [1, 2, 3]
    assert stream.opened_with == [None, "a2"]
    assert reader.reconnects == 1
    assert reader.last_event_id == "a3"


@pytest.mark.parametrize(
    "error",
    [httpx.ReadError("connection reset"), httpx.ReadTimeout("silent"), ConnectionResetError()],
)
def test_the_reader_resumes_after_a_network_error(settings, error):
    clock = FakeClock()
    stream = Connections(
        [*sse_lines([("a1", event(1))]), error],
        sse_lines([("a2", event(2))]),
    )
    reader = StreamReader(settings, opener=stream, sleep=clock.sleep)
    edits = list(reader.edits(stop=lambda: reader.kept >= 2))
    assert [e.rev_id for e in edits] == [1, 2]
    assert stream.opened_with == [None, "a1"]
    assert reader.reconnects == 1
    assert clock.sleeps == [2.0]


def test_the_reader_gives_up_after_too_many_failures_in_a_row(settings):
    clock = FakeClock()
    stream = Connections(*[[httpx.ConnectError("refused")] for _ in range(6)])
    reader = StreamReader(settings, opener=stream, sleep=clock.sleep)
    with pytest.raises(FeedError, match="dropped 6 times in a row"):
        list(reader.edits(stop=lambda: False))
    assert len(stream.opened_with) == 6
    assert clock.sleeps == [2.0, 4.0, 8.0, 16.0, 30.0]


def test_failures_with_events_between_them_are_not_in_a_row(settings):
    clock = FakeClock()
    script = []
    for n in range(1, 9):
        script += [[httpx.ConnectError("refused")], sse_lines([(f"a{n}", event(n))])]
    reader = StreamReader(settings, opener=Connections(*script), sleep=clock.sleep)
    edits = list(reader.edits(stop=lambda: reader.kept >= 8))
    assert [e.rev_id for e in edits] == list(range(1, 9))
    assert clock.sleeps == [2.0] * 8


def test_the_reader_does_not_connect_when_told_to_stop_first(settings):
    stream = Connections(sse_lines([("a1", event(1))]))
    reader = StreamReader(settings, opener=stream)
    assert list(reader.edits(stop=lambda: True)) == []
    assert stream.opened_with == []


def test_the_reader_stops_in_the_middle_of_a_connection(settings):
    stream = Connections(sse_lines([(f"a{n}", event(n)) for n in range(1, 6)]))
    reader = StreamReader(settings, opener=stream)
    edits = list(reader.edits(stop=lambda: reader.kept >= 2))
    assert [e.rev_id for e in edits] == [1, 2]
    assert reader.last_event_id == "a2"
    assert reader.reconnects == 0


def test_the_reader_counts_what_it_saw_and_what_it_kept(settings):
    events = [
        ("a1", event(1)),
        ("a2", event(2, wiki="frwiki")),
        ("a3", event(3, bot=True)),
        ("a4", event(4)),
    ]
    reader = StreamReader(settings, opener=Connections(sse_lines(events)))
    edits = list(reader.edits(stop=lambda: reader.events >= 4))
    assert [e.rev_id for e in edits] == [1, 4]
    assert (reader.events, reader.kept) == (4, 2)


def test_an_event_that_is_not_json_fails_loudly(settings):
    reader = StreamReader(settings, opener=Connections(["id: a1", "data: {not json", ""]))
    with pytest.raises(FeedError, match="not JSON"):
        list(reader.edits(stop=lambda: False))


# -- recording and playing back -------------------------------------------------------


def endless_stream(clock: FakeClock, seconds_per_event: float = 1.0):
    """An opener whose stream never ends. Each event takes `seconds_per_event` to arrive."""

    def opener(last_event_id):
        number = int(last_event_id or 0)
        while True:
            number += 1
            clock.advance(seconds_per_event)
            summary = f"Undid revision {number - 1} by {ACCOUNT} (talk)"
            yield from sse_lines([(str(number), event(number, comment=summary))])

    return opener


def test_a_recording_has_one_line_per_edit_no_account_name_and_ends_at_the_deadline(
    settings, tmp_path
):
    clock = FakeClock()
    reader = StreamReader(settings, opener=endless_stream(clock), sleep=clock.sleep)
    path = tmp_path / "recordings" / "slice.jsonl"
    assert record(reader, path, 5.0, clock=clock) == 5
    text = path.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines()]
    assert [row["rev_id"] for row in rows] == [1, 2, 3, 4, 5]
    assert all(set(row) == set(Edit.model_fields) for row in rows)
    assert ACCOUNT not in text
    assert clock() == 5.0


def test_recording_reports_progress_every_hundred_edits(settings, tmp_path):
    clock = FakeClock()
    reader = StreamReader(settings, opener=endless_stream(clock, 0.1), sleep=clock.sleep)
    said = []
    record(reader, tmp_path / "slice.jsonl", 25.05, clock=clock, progress=said.append)
    assert said == ["100 edits recorded", "200 edits recorded"]


def test_a_recording_loads_back_as_the_edits_that_were_read(settings, tmp_path):
    clock = FakeClock()
    reader = StreamReader(settings, opener=endless_stream(clock), sleep=clock.sleep)
    path = tmp_path / "slice.jsonl"
    record(reader, path, 3.0, clock=clock)
    expected = [
        parse_event(event(n, comment=f"Undid revision {n - 1} by {ACCOUNT} (talk)"), settings)
        for n in (1, 2, 3)
    ]
    assert load(path) == expected
    assert all(e.event_time is not None for e in load(path))


def arriving(*seconds: float) -> list[Edit]:
    """Edits whose events arrived this many seconds after T0."""
    return [
        make_edit(n, event_time=at(0) + timedelta(seconds=s))
        for n, s in enumerate(seconds, start=1)
    ]


def test_arrival_offsets_count_from_the_first_event_and_never_run_backwards():
    assert arrival_offsets(arriving(0, 5, 3, 9, 8.5)) == [0.0, 5.0, 5.0, 9.0, 9.0]
    assert arrival_offsets([]) == []


def test_arrival_offsets_fall_back_to_the_time_of_the_edit():
    assert arrival_offsets(
        [make_edit(1, minute=0), make_edit(2, minute=1), make_edit(3, minute=3)]
    ) == [0.0, 60.0, 180.0]


@pytest.mark.parametrize(
    ("speed", "waits"), [(1.0, [4.0, 6.0]), (2.0, [2.0, 3.0]), (10.0, [0.4, 0.6])]
)
def test_replay_sleeps_the_recorded_gaps_divided_by_the_speed(speed, waits):
    clock = FakeClock()
    edits = arriving(0, 4, 10)
    assert list(replay(edits, speed, sleep=clock.sleep, clock=clock)) == edits
    assert clock.sleeps == pytest.approx(waits)


def test_replay_does_not_wait_for_time_the_reader_already_used():
    clock = FakeClock()
    for _ in replay(arriving(0, 4, 10), sleep=clock.sleep, clock=clock):
        clock.advance(5.0)
    assert clock.sleeps == []


@pytest.mark.parametrize("speed", [0, -1.0])
def test_replay_rejects_a_speed_that_is_not_positive(speed):
    with pytest.raises(ValueError, match="speed must be positive"):
        list(replay(arriving(0, 4), speed))
