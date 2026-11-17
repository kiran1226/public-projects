"""The live run, the truth collector and the feed health checks, against a fake GitHub."""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest

from triagedesk.budget import Budget
from triagedesk.feed import FeedError
from triagedesk.health import check_fresh, check_labels, check_volume, run_checks
from triagedesk.live import (
    CURSOR,
    collect_live_truth,
    due_for_truth,
    fetch_new,
    live_paths,
    read_runs,
    run_live,
    spent_today,
)
from triagedesk.log import PredictionLog
from triagedesk.models import LabelPick, Triage
from triagedesk.taxonomy import public_form, save
from triagedesk.truth import NOT_FETCHED, RESOLVED, OutcomeLog

from conftest import T0, FakeGitHub, ScriptedProvider, make_client, make_desk, make_issue

NOW = T0 + timedelta(days=120)


def arrival(number, title, hours_ago, **kwargs):
    return make_issue(
        number,
        title,
        f"### Issue description\n{title} every time.",
        day=120 - hours_ago / 24,
        **kwargs,
    )


def scripted(labels=(("topic:gui", "high"),), duplicate=None):
    triage = Triage(
        labels=[LabelPick(name=n, confidence=c) for n, c in labels],
        duplicate_of=duplicate, duplicate_confidence="high", note="Scripted.",
    )  # fmt: skip
    return ScriptedProvider(lambda system, user: triage)


@pytest.fixture
def desk(tmp_path):
    return make_desk(tmp_path, provider=scripted())


def github(*issues, **kwargs):
    fake = FakeGitHub(list(issues), **kwargs)
    return fake, make_client(fake)


def test_the_first_run_looks_back_one_day_and_sets_the_cursor(desk):
    fake, client = github(
        arrival(900, "Old popup bug", 30), arrival(901, "Popup menu loses focus", 5)
    )
    fresh, fetched = fetch_new(desk.settings, desk.store, client, NOW)
    assert [i.number for i in fresh] == [901] and fetched == 1
    assert desk.store.get_state(CURSOR) == fresh[0].created_at.isoformat()


def test_a_later_run_starts_at_the_cursor_and_returns_only_unseen_issues(desk):
    fake, client = github(arrival(901, "Popup menu loses focus", 5))
    fetch_new(desk.settings, desk.store, client, NOW)
    fake.issues.append(arrival(902, "Mixer bus clips", 1))
    fresh, fetched = fetch_new(desk.settings, desk.store, client, NOW)
    assert [i.number for i in fresh] == [902] and fetched == 2


def test_a_known_issue_is_refreshed_but_its_first_sighting_is_kept(desk):
    fake, client = github(arrival(901, "Popup menu loses focus", 5))
    fetch_new(desk.settings, desk.store, client, NOW)
    fake.issues[0] = arrival(901, "Edited title", 5, labels=["topic:gui"])
    fetch_new(desk.settings, desk.store, client, NOW + timedelta(hours=1))
    stored = desk.store.issue(901)
    assert stored.title == "Popup menu loses focus" and stored.labels == ["topic:gui"]
    assert desk.store.first_seen(901)[1] == []


def test_a_run_answers_each_new_issue_under_every_configuration(desk):
    fake, client = github(
        arrival(901, "Popup menu loses focus", 5), arrival(902, "Mixer bus clips", 1)
    )
    summary = run_live(desk, client, now=NOW)
    assert summary["new_issues"] == 2 and summary["predictions"] == 6
    assert summary["mode"] == "shadow" and summary["applied"] == 0
    rows = PredictionLog(live_paths(desk.settings)["log"]).read()
    assert {(p.number, p.config) for p in rows} == {
        (n, c) for n in (901, 902) for c in ("rules", "classifier", "model")
    }
    assert all(p.written_at == NOW for p in rows)


def test_a_second_run_with_nothing_new_writes_nothing(desk):
    fake, client = github(arrival(901, "Popup menu loses focus", 5))
    run_live(desk, client, now=NOW)
    again = run_live(desk, client, now=NOW + timedelta(hours=1))
    assert again["new_issues"] == 0 and again["predictions"] == 0
    assert len(read_runs(desk.settings)) == 2


def test_an_issue_already_labelled_or_closed_is_flagged_in_its_row(desk):
    fake, client = github(
        arrival(901, "Popup menu loses focus", 5, labels=["topic:gui"]),
        arrival(902, "Mixer bus clips", 4, labels=["bug"]),
        arrival(903, "Raycast misses", 3, state="closed", state_reason="duplicate"),
    )
    summary = run_live(desk, client, now=NOW)
    rows = {p.number: p for p in PredictionLog(live_paths(desk.settings)["log"]).read()}
    assert rows[901].seen_labels and not rows[901].seen_closed
    assert not rows[902].seen_labels, "a label outside the taxonomy gives nothing away"
    assert rows[903].seen_closed
    assert summary["already_labelled_or_closed"] == 2


def test_the_pipeline_never_reads_the_labels_the_tracker_already_shows(tmp_path):
    provider = scripted()
    desk = make_desk(tmp_path, provider=provider)
    fake, client = github(
        arrival(901, "Popup menu loses focus", 5, labels=["topic:audio", "platform:web"])
    )
    run_live(desk, client, now=NOW)
    system, user = provider.calls[0]
    assert "topic:audio" not in user and "platform:web" not in user


def test_without_a_model_provider_the_key_free_configurations_still_run(tmp_path):
    desk = make_desk(tmp_path)
    fake, client = github(arrival(901, "Popup menu loses focus", 5))
    summary = run_live(desk, client, now=NOW)
    assert summary["configs"] == ["rules", "classifier"]
    assert summary["model_configs_skipped"] == ["model"]
    assert summary["predictions"] == 2


def test_at_the_daily_cap_the_classifier_answers_and_the_refusals_are_counted(tmp_path):
    budget = Budget(0.50, spent_usd=0.50)
    desk = make_desk(tmp_path, provider=scripted(), budget=budget)
    fake, client = github(arrival(901, "Popup menu loses focus", 5))
    summary = run_live(desk, client, now=NOW, budget=budget)
    rows = {p.config: p for p in PredictionLog(live_paths(desk.settings)["log"]).read()}
    assert rows["model"].proposal.source == "classifier"
    assert "model_unavailable" in rows["model"].proposal.person
    assert summary["calls_refused_at_cap"] == 1 and summary["cost_usd"] == 0


def test_spent_today_counts_only_todays_rows(desk):
    fake, client = github(arrival(901, "Popup menu loses focus", 5))
    run_live(desk, client, now=NOW)
    log = PredictionLog(live_paths(desk.settings)["log"])
    assert spent_today(log, NOW) == pytest.approx(0.00033)
    assert spent_today(log, NOW + timedelta(days=1)) == 0.0


def test_a_feed_error_stops_the_run_before_anything_is_logged(desk):
    fake, client = github()
    fake.fail_next = [httpx.Response(200, json={"unexpected": True})]
    with pytest.raises(FeedError):
        run_live(desk, client, now=NOW)
    assert not live_paths(desk.settings)["log"].exists()
    assert read_runs(desk.settings) == []


class RecordingWriter:
    def __init__(self):
        self.plans = []

    def apply(self, planned):
        self.plans.append(planned)
        return ["labels"]


def test_apply_mode_acts_once_per_new_issue_on_the_last_configuration(desk):
    fake, client = github(arrival(901, "Popup menu loses focus", 5))
    writer = RecordingWriter()
    summary = run_live(desk, client, now=NOW, writer=writer)
    assert summary["mode"] == "apply" and summary["applied"] == 1
    assert [(p.number, p.add_labels) for p in writer.plans] == [(901, ["topic:gui"])]
    run_live(desk, client, now=NOW + timedelta(hours=1), writer=writer)
    assert len(writer.plans) == 1


def test_apply_mode_does_nothing_for_an_answer_that_goes_to_a_person(tmp_path):
    desk = make_desk(tmp_path, provider=scripted(labels=(("topic:gui", "medium"),)))
    fake, client = github(arrival(901, "Popup menu loses focus", 5))
    writer = RecordingWriter()
    assert run_live(desk, client, now=NOW, writer=writer)["applied"] == 0
    assert writer.plans == []


# -- truth, fourteen days later ------------------------------------------------------


def start_live(desk, *issues, timelines=None):
    fake, client = github(*issues, timelines=timelines)
    run_live(desk, client, now=NOW)
    save(public_form(desk.taxonomy), desk.settings.home / "data" / "taxonomy-live.json")
    return fake, client


def test_no_outcome_is_read_before_the_waiting_period_is_over(desk):
    fake, client = start_live(desk, arrival(901, "Popup menu loses focus", 5))
    early = NOW + timedelta(days=13)
    assert collect_live_truth(desk.settings, desk.store, client, now=early)["due"] == 0
    assert not live_paths(desk.settings)["outcomes"].exists()


def test_outcomes_are_read_once_the_waiting_period_is_over(desk):
    fake, client = start_live(desk, arrival(901, "Popup menu loses focus", 5))
    fake.issues[0] = arrival(901, "Popup menu loses focus", 5, labels=["topic:gui", "bug"])
    later = NOW + timedelta(days=15)
    result = collect_live_truth(desk.settings, desk.store, client, now=later)
    assert result == {"due": 1, "written": 1, "waiting_for_timeline": 0}
    outcome = OutcomeLog(live_paths(desk.settings)["outcomes"]).read()[901]
    assert outcome.labels == ["topic:gui"] and outcome.read_at == later
    assert collect_live_truth(desk.settings, desk.store, client, now=later)["due"] == 0


def test_a_live_duplicate_gets_its_original_from_the_timeline(desk):
    fake, client = start_live(desk, arrival(901, "Popup menu loses focus", 5))
    fake.issues[0] = arrival(
        901, "Popup menu loses focus", 5, state="closed", state_reason="duplicate"
    )
    fake.timelines = {
        901: [
            {
                "event": "commented",
                "created_at": "2026-05-02T00:00:00Z",
                "body": "Duplicate of #12",
            },
            {"event": "closed", "created_at": "2026-05-02T00:00:05Z", "state_reason": "duplicate"},
        ]
    }
    collect_live_truth(desk.settings, desk.store, client, now=NOW + timedelta(days=15))
    outcome = OutcomeLog(live_paths(desk.settings)["outcomes"]).read()[901]
    assert (outcome.duplicate, outcome.original, outcome.original_status) == (True, 12, RESOLVED)


def test_a_duplicate_whose_timeline_could_not_be_fetched_is_tried_again(desk):
    fake, client = start_live(desk, arrival(901, "Popup menu loses focus", 5))
    fake.issues[0] = arrival(
        901, "Popup menu loses focus", 5, state="closed", state_reason="duplicate"
    )
    later = NOW + timedelta(days=15)
    limited = make_client(fake, max_wait_s=10, clock=lambda: 1000.0)
    original_handler = fake.handler

    def no_core_quota(request):
        if request.url.path.endswith("/timeline"):
            return httpx.Response(
                403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "4600"}
            )
        return original_handler(request)

    limited._http = httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(no_core_quota)
    )
    first = collect_live_truth(desk.settings, desk.store, limited, now=later)
    assert first["waiting_for_timeline"] == 1
    log = OutcomeLog(live_paths(desk.settings)["outcomes"])
    assert log.read()[901].original_status == NOT_FETCHED
    assert 901 in due_for_truth(
        desk.settings, PredictionLog(live_paths(desk.settings)["log"]), log, later
    )
    fake.timelines = {
        901: [
            {"event": "commented", "created_at": "2026-05-02T00:00:00Z", "body": "Duplicate of #12"}
        ]
    }
    collect_live_truth(desk.settings, desk.store, client, now=later + timedelta(hours=1))
    assert log.read()[901].original == 12


# -- health ---------------------------------------------------------------------------


def test_a_recent_issue_means_the_feed_is_fresh():
    assert check_fresh([arrival(1, "t", 3)], NOW).ok
    stale = check_fresh([arrival(1, "t", 72)], NOW)
    assert not stale.ok and "72 hours" in stale.detail
    assert not check_fresh([], NOW).ok


def test_a_week_far_below_the_usual_volume_is_flagged(store):
    store.add_issues([make_issue(n, "t", day=120 - 8 - n * 0.5) for n in range(1, 113)])
    recent = [arrival(1000 + n, "t", n) for n in range(10)]
    assert check_volume(recent, store, NOW).ok
    low = check_volume(recent[:3], store, NOW)
    assert not low.ok and "3 issues" in low.detail


def test_volume_passes_when_there_is_no_history_to_compare_with(store):
    assert check_volume([], store, NOW).ok


def test_a_label_the_taxonomy_has_never_seen_is_flagged(desk):
    known = [arrival(1, "t", 1, labels=["topic:gui", "bug", "platform:web"])]
    assert check_labels(known, desk.settings, desk.taxonomy).ok
    new = check_labels(
        [arrival(2, "t", 1, labels=["topic:shiny-new"])], desk.settings, desk.taxonomy
    )
    assert not new.ok and "topic:shiny-new" in new.detail


def test_a_label_that_was_too_rare_to_keep_is_not_a_surprise(desk):
    desk.taxonomy.dropped["topic:rare"] = 2
    assert check_labels(
        [arrival(2, "t", 1, labels=["topic:rare"])], desk.settings, desk.taxonomy
    ).ok


def test_all_checks_pass_on_a_healthy_feed(desk):
    fake, client = github(*[arrival(900 + n, "Popup", n * 10) for n in range(8)])
    result = run_checks(desk.settings, desk.store, client, desk.taxonomy, NOW)
    assert result["ok"]
    assert [c["name"] for c in result["checks"]] == [
        "feed_answers",
        "feed_fresh",
        "volume",
        "labels_known",
    ]
    json.dumps(result)


def test_a_response_that_no_longer_parses_fails_the_first_check_only(desk):
    fake, client = github()
    fake.fail_next = [httpx.Response(200, json={"items": "changed"})]
    result = run_checks(desk.settings, desk.store, client, desk.taxonomy, NOW)
    assert not result["ok"]
    assert [c["name"] for c in result["checks"]] == ["feed_answers"]


def test_a_silent_feed_is_a_failure_not_a_quiet_day(desk):
    fake, client = github()
    result = run_checks(desk.settings, desk.store, client, desk.taxonomy, NOW)
    assert not result["ok"]
    assert {c["name"]: c["ok"] for c in result["checks"]}["feed_fresh"] is False
