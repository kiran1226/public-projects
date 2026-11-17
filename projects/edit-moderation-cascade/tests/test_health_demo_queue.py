"""Feed health, the demo on the invented fixture, and the queue as the terminal shows it."""

from __future__ import annotations

import httpx
import pytest

from editwatch.combine import DEFAULT
from editwatch.config import CATEGORIES, Settings
from editwatch.demo import FIXTURE_SHARE, fixture_tier1, load_fixture, run_demo
from editwatch.health import MAX_LAG_S, check_lag, check_rate, read, run_checks, write
from editwatch.models import Change
from editwatch.queue import QueueItem, build_queue, format_item
from editwatch.stream import parse_event
from editwatch.tier1 import SHARES
from editwatch.tier2 import Tier2
from editwatch.wiki import FeedError

from conftest import (
    ACCOUNT,
    CALL_COST,
    FakeClock,
    FakeWiki,
    ScriptedProvider,
    answer,
    at,
    make_client,
    make_edit,
    make_prediction,
    stream_event,
)

# -- feed health: the rate and the lag -----------------------------------------------


def test_the_usual_rates_pass_both_rate_checks_and_the_details_say_what_was_heard():
    volume, kept = check_rate(events=140, kept=20, seconds=20.0)
    assert (volume.name, volume.ok, kept.name, kept.ok) == ("stream_volume", True, "edits_kept", True)  # fmt: skip
    assert volume.detail.startswith("140 events in 20 seconds")
    assert kept.detail.startswith("20 article edits by people in 20 seconds")
    assert [check.ok for check in check_rate(events=20, kept=4, seconds=20.0)] == [True, True]


@pytest.mark.parametrize(
    ("events", "kept", "passes"),
    [(0, 0, [False, False]), (140, 3, [True, False]), (19, 19, [False, True])],
    ids=["a silent stream", "a stream with too few article edits", "a thin stream"],
)
def test_a_silent_or_thin_stream_fails_its_rate_check(events, kept, passes):
    assert [check.ok for check in check_rate(events, kept, seconds=20.0)] == passes


def heard(rev_id: int, seconds_before_now: float | None):
    """An edit whose event was delivered that long before `at(10)`, or carries no time."""
    event_time = None if seconds_before_now is None else at(10, seconds=-seconds_before_now)
    return make_edit(rev_id, minute=9, event_time=event_time)


def test_the_lag_is_measured_from_the_newest_event():
    check = check_lag([heard(1, 500), heard(2, 30), heard(3, 90), heard(4, None)], at(10))
    assert (check.name, check.ok) == ("stream_lag", True)
    assert check.detail == "The newest event is 30 seconds old (limit 120)."


def test_events_older_than_the_limit_fail_the_lag_check():
    assert MAX_LAG_S == 120.0
    assert check_lag([heard(1, 120)], at(10)).ok
    late = check_lag([heard(1, 121), heard(2, 400)], at(10))
    assert not late.ok and "121 seconds old" in late.detail


def test_the_lag_check_fails_when_no_event_carries_a_time():
    for edits in ([], [heard(1, None), heard(2, None)]):
        check = check_lag(edits, at(10))
        assert (check.name, check.ok, check.detail) == ("stream_lag", False, "No event carried its own time.")  # fmt: skip


# -- feed health: a run of the checks ------------------------------------------------


class FakeReader:
    """Stands in for the stream reader: yields the edits it was given, then fails if told to.

    With a clock, every edit takes `step` seconds to arrive, and it stops when asked.
    """

    def __init__(self, edits=(), *, events: int = 150, error=None, clock=None, step: float = 0.0):
        self._edits = list(edits)
        self._events = events
        self._error = error
        self._clock = clock
        self._step = step
        self.events = 0
        self.kept = 0

    def edits(self, stop):
        for edit in self._edits:
            if stop():
                break
            if self._clock is not None:
                self._clock.advance(self._step)
            self.kept += 1
            yield edit
        self.events = self._events
        if self._error is not None:
            raise self._error


NOW = at(10)
HEARD = [heard(rev, 40 - rev % 10) for rev in (901, 902, 903, 904, 905)]


def checks_for(reader, fake=None, **kwargs):
    fake = fake if fake is not None else FakeWiki(tags={905: ["mobile edit"]})
    kwargs.setdefault("clock", FakeClock())
    result = run_checks(reader, make_client(fake), now=NOW, **kwargs)
    return result, {check["name"]: check for check in result["checks"]}, fake


def test_a_healthy_feed_passes_every_check_by_name():
    result, checks, _ = checks_for(FakeReader(HEARD))
    assert list(checks) == ["stream_answers", "stream_volume", "edits_kept", "stream_lag", "wiki_answers"]  # fmt: skip
    assert all(check["ok"] for check in checks.values()) and result["ok"] is True
    assert result["checked_at"] == "2026-11-01T00:10:00+00:00"
    assert all(set(check) == {"name", "ok", "detail"} for check in result["checks"])
    assert checks["edits_kept"]["detail"].startswith("5 article edits by people in 20 seconds")


def test_the_wiki_is_asked_once_for_the_tags_of_the_newest_edit_heard():
    _, _, fake = checks_for(FakeReader(HEARD))
    assert fake.revids_asked() == [[905]]
    assert fake.asked("revisions")[0]["rvprop"] == "ids|tags"


def test_listening_stops_when_the_injected_clock_reaches_the_deadline():
    clock = FakeClock()
    edits = [heard(rev, 30) for rev in range(901, 911)]
    reader = FakeReader(edits, clock=clock, step=5.0)
    fake = FakeWiki(tags={rev: [] for rev in range(901, 911)})
    result, checks, _ = checks_for(reader, fake, clock=clock, seconds=20.0)
    assert reader.kept == 4
    assert fake.revids_asked() == [[904]]
    assert checks["edits_kept"]["ok"] and result["ok"]


def test_a_feed_error_fails_only_the_first_check_and_nothing_else_is_checked():
    reader = FakeReader(error=FeedError("A stream event no longer parses: 'revision'"))
    result, checks, fake = checks_for(reader)
    assert result["ok"] is False
    assert result["checks"] == [
        {
            "name": "stream_answers",
            "ok": False,
            "detail": "A stream event no longer parses: 'revision'",
        }  # fmt: skip
    ]
    assert fake.calls == []


def test_a_feed_error_after_some_edits_skips_the_rate_and_the_lag_and_still_asks_the_wiki():
    reader = FakeReader(HEARD[:2], error=FeedError("A stream event is not JSON."))
    result, checks, fake = checks_for(reader, FakeWiki(tags={902: []}))
    assert list(checks) == ["stream_answers", "wiki_answers"]
    assert (checks["stream_answers"]["ok"], checks["wiki_answers"]["ok"]) == (False, True)
    assert result["ok"] is False and fake.revids_asked() == [[902]]


def test_a_stream_that_delivers_nothing_fails_the_rate_and_the_lag_and_the_wiki_is_not_asked():
    result, checks, fake = checks_for(FakeReader([], events=0))
    assert {name: check["ok"] for name, check in checks.items()} == {
        "stream_answers": True,
        "stream_volume": False,
        "edits_kept": False,
        "stream_lag": False,
    }
    assert result["ok"] is False and fake.calls == []


def test_a_wiki_that_does_not_know_the_newest_edit_fails_the_last_check():
    result, checks, _ = checks_for(FakeReader(HEARD), FakeWiki(tags={904: []}))
    assert checks["wiki_answers"] == {
        "name": "wiki_answers",
        "ok": False,
        "detail": "The wiki did not return the newest edit.",
    }
    assert result["ok"] is False
    assert all(checks[name]["ok"] for name in ("stream_answers", "stream_volume", "stream_lag"))


def test_a_wiki_that_answers_with_an_error_fails_the_last_check_with_the_reason():
    fake = FakeWiki(tags={905: []})
    fake.fail_next = [httpx.Response(404, json={"message": "Not Found"})]
    result, checks, _ = checks_for(FakeReader(HEARD), fake)
    assert checks["wiki_answers"]["ok"] is False and "HTTP 404" in checks["wiki_answers"]["detail"]
    assert result["ok"] is False


def test_a_health_result_is_written_and_read_back(tmp_path):
    result, _, _ = checks_for(FakeReader(HEARD))
    path = tmp_path / "scoreboard" / "health.json"
    assert read(path) is None
    write(result, path)
    assert read(path) == result


# -- the demo ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def project() -> Settings:
    """The project itself: the demo reads the committed fixture and writes nothing."""
    return Settings()


@pytest.fixture(scope="module")
def fixture(project):
    return load_fixture(project.fixture_dir)


@pytest.fixture(scope="module")
def tier1(project):
    return fixture_tier1(project)


@pytest.fixture(scope="module")
def threshold(tier1) -> float:
    return tier1.threshold(FIXTURE_SHARE)


@pytest.fixture(scope="module")
def tier1_only(project):
    return run_demo(project, None)


@pytest.fixture(scope="module")
def with_model(project):
    provider = ScriptedProvider(lambda system, user: answer(90, "vandalism", 1, 1))
    return provider, *run_demo(project, Tier2(provider))


def in_order(queue) -> bool:
    keys = [(-p.verdict.risk, p.rev_id) for _, p, _ in queue]
    return keys == sorted(keys)


def test_the_fixture_is_300_edits_with_an_outcome_each_and_a_change_for_forty(fixture):
    edits, reverted, changes = fixture
    assert len(edits) == 300 and len({e.rev_id for e in edits}) == 300
    assert set(reverted) == {e.rev_id for e in edits}
    assert len(changes) == 40 and set(changes) <= set(reverted)
    assert all(change.lines and change.rev_id == rev for rev, change in changes.items())
    assert 0 < sum(reverted.values()) < 300


def test_the_fixtures_arrivals_are_edits_by_people_made_after_all_of_its_history(fixture):
    edits, _, changes = fixture
    history = [e for e in edits if e.rev_id not in changes]
    arrivals = [e for e in edits if e.rev_id in changes]
    assert max(e.timestamp for e in history) < min(e.timestamp for e in arrivals)
    assert not any(e.bot for e in edits)


def test_the_fixture_holds_no_mention_no_link_and_no_temporary_account_name(project):
    text = (project.fixture_dir / "edits.jsonl").read_text("utf-8")
    for trace in ("@", "http", "~20"):
        assert trace not in text, trace


def test_the_fixtures_tier_one_trains_on_its_history_and_not_on_the_arrivals(tier1):
    assert tier1.trained_on == 260
    assert tuple(tier1.quantiles) == SHARES
    assert 0 < tier1.reverted_share < 1


def test_without_a_model_the_demo_orders_every_arrival_by_tier_one(tier1_only, fixture):
    queue, outcomes, _ = tier1_only
    _, reverted, changes = fixture
    assert {e.rev_id for e, _, _ in queue} == set(changes) == set(outcomes)
    assert all(outcomes[rev] == reverted[rev] for rev in outcomes)
    assert all(e.rev_id == p.rev_id == c.rev_id for e, p, c in queue)
    for _, p, _ in queue:
        assert (p.verdict.source, p.verdict.risk, p.cost_usd) == ("tier1", p.verdict.tier1, 0.0)
    assert in_order(queue)


def test_without_a_model_both_configurations_score_the_same_queue(tier1_only):
    _, outcomes, scores = tier1_only
    cascade, tier1 = scores
    assert (cascade.config, tier1.config) == ("cascade", "tier1")
    assert cascade.system_version != tier1.system_version
    assert cascade.edits == tier1.edits == 40
    assert cascade.reverted == tier1.reverted == sum(outcomes.values())
    assert cascade.hits == tier1.hits and cascade.model_read == 0


def test_with_a_model_the_edits_in_the_slice_are_decided_by_the_model(with_model, threshold):
    provider, queue, _, _ = with_model
    decided = {p.rev_id for _, p, _ in queue if p.verdict.source == "model"}
    assert decided == {p.rev_id for _, p, _ in queue if p.verdict.tier1 >= threshold}
    assert 0 < len(decided) < 40 and len(provider.calls) == len(decided)
    for _, p, _ in queue:
        if p.rev_id in decided:
            assert (p.verdict.model_risk, p.verdict.category) == (90, "vandalism")
            assert p.verdict.risk == DEFAULT.risk(p.verdict.tier1, 90)
            assert (p.verdict.first_line, p.verdict.last_line) == (1, 1)
        else:
            assert (p.verdict.risk, p.verdict.skipped) == (p.verdict.tier1, None)


def test_with_a_model_the_queue_is_riskiest_first_and_the_score_counts_reads_and_cost(with_model):
    provider, queue, _, scores = with_model
    cascade, tier1 = scores
    assert in_order(queue) and len(queue) == 40
    assert queue[0][1].verdict.source == "model" and queue[-1][1].verdict.source == "tier1"
    assert cascade.model_read == len(provider.calls) and tier1.model_read == 0
    assert cascade.cost_usd == pytest.approx(len(provider.calls) * CALL_COST)
    assert tier1.cost_usd == 0.0 and cascade.categories == {"vandalism": len(provider.calls)}


# -- the queue -----------------------------------------------------------------------

FERRY = Change(
    rev_id=501,
    lines=[
        "1 - The ferry to Gullrock runs twice a day.",
        "2 + The ferry to Gullrock runs twice a week.",
        "3 + Its captain is a seagull.",
        "4 + == Timetable ==",
    ],
)


def item(place: int = 7, *, reverted=None, change=FERRY, edit=None, **prediction) -> QueueItem:
    edit = edit or make_edit(501, title="Gullrock ferry", temp=True, old_len=5200, new_len=5310)
    risk = prediction.pop("risk", 0.87)
    return QueueItem(place, edit, make_prediction(501, risk, **prediction), change, reverted)


def test_the_sentence_of_an_edit_the_model_read_is_worded_from_the_category():
    sentences = {
        "vandalism": "Looks like vandalism.",
        "spam_or_promotion": "Looks like promotion or spam.",
        "false_or_unsourced_claim": "Changes a fact without a source.",
        "test_or_accident": "Looks like a test or an accident.",
        "content_removal": "Removes content without a clear reason.",
        "other": "The model saw a problem it could not name.",
        "none": "The model saw no problem.",
    }
    assert set(sentences) == set(CATEGORIES)
    for category, sentence in sentences.items():
        assert item(model_risk=60, category=category).sentence == sentence


def test_a_category_the_page_has_no_words_for_is_a_problem_the_model_could_not_name():
    unknown = item(model_risk=60, category="approved_by_admin")
    assert unknown.sentence == "The model saw a problem it could not name."


def test_the_sentence_of_an_edit_tier_one_decided_says_why_the_model_did_not():
    reasons = {
        None: "Not in the slice the model reads.",
        "queue_full": "The model's queue was full.",
        "budget": "The day's budget was used up.",
        "no_model": "No model was available in this run.",
        "model_error": "The model gave no answer.",
        "gate": "The model's answer failed a check.",
        "window_closed": "The run ended before the model was asked.",
        "a_reason_added_later": "The model was not asked.",
    }
    for skipped, why in reasons.items():
        assert item(skipped=skipped).sentence == f"Ordered by tier 1 alone. {why}"


def test_marked_lists_the_numbers_of_the_lines_the_model_marked():
    assert item(model_risk=90, lines=(2, 3)).marked == [2, 3]
    assert item(model_risk=90, lines=(4, 4)).marked == [4]
    assert item(model_risk=90).marked == []
    assert item().marked == []


def test_the_queue_is_riskiest_first_with_ties_by_revision_and_places_from_one():
    risks = {30: 0.4, 10: 0.9, 40: 0.4, 20: 0.4, 50: 0.95}
    rows = [(make_edit(rev), make_prediction(rev, risk), None) for rev, risk in risks.items()]
    queue = build_queue(rows)
    assert [(q.place, q.edit.rev_id) for q in queue] == [(1, 50), (2, 10), (3, 20), (4, 30), (5, 40)]  # fmt: skip
    assert all(q.prediction.rev_id == q.edit.rev_id for q in queue)
    assert build_queue([]) == []


def test_the_queue_carries_the_change_and_the_outcome_where_it_is_known():
    rows = [
        (make_edit(1), make_prediction(1, 0.9), FERRY),
        (make_edit(2), make_prediction(2, 0.8), None),
        (make_edit(3), make_prediction(3, 0.7), None),
    ]
    queue = build_queue(rows, {1: True, 2: False})
    assert [q.reverted for q in queue] == [True, False, None]
    assert [q.change for q in queue] == [FERRY, None, None]
    assert [q.reverted for q in build_queue(rows)] == [None, None, None]


@pytest.mark.parametrize(
    ("temp", "old_len", "new_len", "first_line"),
    [
        (True, 5200, 5310, "  7. risk 0.87  Gullrock ferry  (+110 bytes, temporary account)"),
        (False, 5200, 4100, "  7. risk 0.87  Gullrock ferry  (-1,100 bytes, registered account)"),
    ],
)
def test_an_entry_starts_with_the_place_the_risk_the_title_the_size_and_the_kind_of_account(
    temp, old_len, new_len, first_line
):
    edit = make_edit(501, title="Gullrock ferry", temp=temp, old_len=old_len, new_len=new_len)
    assert format_item(item(edit=edit)).splitlines()[0] == first_line


def test_an_entry_the_model_read_shows_the_sentence_both_risks_and_the_marked_lines():
    text = format_item(item(tier1=0.62, model_risk=90, category="vandalism", lines=(2, 3)))
    assert text.splitlines()[1:] == [
        "     Looks like vandalism. Model risk 90, tier 1 0.62.",
        "        1 - The ferry to Gullrock runs twice a day.",
        "     >> 2 + The ferry to Gullrock runs twice a week.",
        "     >> 3 + Its captain is a seagull.",
        "        4 + == Timetable ==",
    ]


def test_an_entry_tier_one_decided_says_so_and_marks_no_line():
    text = format_item(item(skipped="queue_full"))
    assert text.splitlines()[1] == "     Ordered by tier 1 alone. The model's queue was full."
    assert ">>" not in text and "Model risk" not in text
    assert format_item(item(change=None)).count("\n") == 1


def test_an_entry_names_the_gate_rules_that_fired_and_the_outcome_when_it_is_known():
    rejected = item(skipped="gate", gate=["RISK_OUT_OF_RANGE", "RANGE_OUTSIDE_CHANGE"], reverted=True)  # fmt: skip
    assert format_item(rejected).splitlines()[-2:] == [
        "     gate: RISK_OUT_OF_RANGE, RANGE_OUTSIDE_CHANGE",
        "     outcome: reverted",
    ]
    assert format_item(item(reverted=False)).splitlines()[-1] == "     outcome: kept"
    plain = format_item(item())
    assert "gate:" not in plain and "outcome:" not in plain


def test_a_long_line_of_the_change_is_cut_in_the_entry():
    long = Change(rev_id=501, lines=["1 + " + "ab" * 200])
    shown = format_item(item(change=long, model_risk=90, lines=(1, 1))).splitlines()[-1]
    assert shown == "     >> 1 + " + ("ab" * 200)[:146]


@pytest.mark.parametrize(
    ("user", "kind"), [(ACCOUNT, "registered account"), ("~2026-12345-6", "temporary account")]
)
def test_an_entry_never_shows_an_account_name_from_the_event_to_the_terminal(user, kind):
    event = stream_event(make_edit(501, title="Gullrock ferry", comment=f"as @{user} asked"), user=user)  # fmt: skip
    edit = parse_event(event, Settings())
    text = format_item(item(edit=edit, model_risk=90, category="vandalism", lines=(2, 3)))
    assert kind in text
    assert user not in text and "~2026" not in text
