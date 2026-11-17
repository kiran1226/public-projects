"""The GitHub client: parsing, windows, quotas, and what it refuses to read."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from triagedesk.backfill import fetch_range, month_ranges
from triagedesk.feed import (
    FeedError,
    RateLimited,
    parse_issue,
    parse_timeline,
    references,
)
from triagedesk.models import Issue

from conftest import REPO, T0, FakeGitHub, api_item, make_client, make_issue


def test_parse_issue_keeps_text_dates_and_labels():
    issue = make_issue(7, "Crash on export", "It crashes.", labels=["topic:export", "bug"])
    parsed = parse_issue(api_item(issue))
    assert parsed.number == 7
    assert parsed.title == "Crash on export"
    assert parsed.labels == ["bug", "topic:export"]
    assert parsed.created_at == T0


def test_parse_issue_reads_no_person_fields():
    parsed = parse_issue(api_item(make_issue(7, "t")))
    assert "someone" not in parsed.model_dump_json()
    assert set(Issue.model_fields) == {
        "number", "title", "body", "created_at", "updated_at", "closed_at", "state",
        "state_reason", "labels",
    }  # fmt: skip


def test_parse_issue_treats_a_missing_body_as_empty():
    assert parse_issue(api_item(make_issue(7, "t"), body=None)).body == ""


@pytest.mark.parametrize("missing", ["number", "title", "created_at", "state"])
def test_parse_issue_fails_loudly_when_a_required_field_is_gone(missing):
    item = api_item(make_issue(7, "t"))
    del item[missing]
    with pytest.raises(FeedError, match="no longer parses"):
        parse_issue(item)


def test_closed_as_duplicate_needs_both_state_and_reason():
    assert make_issue(1, "t", state="closed", state_reason="duplicate").closed_as_duplicate
    assert not make_issue(1, "t", state="closed", state_reason="completed").closed_as_duplicate
    assert not make_issue(1, "t", state="open", state_reason="duplicate").closed_as_duplicate


@pytest.mark.parametrize(
    ("text", "inside", "outside"),
    [
        ("Duplicate of #124570", [124570], []),
        ("Duplicate of https://github.com/example/engine/issues/42", [42], []),
        ("See example/engine#9 and #10", [9, 10], []),
        (
            "Closing as duplicate of https://github.com/example/proposals/issues/3126",
            [],
            ["example/proposals#3126"],
        ),
        ("Fixed by other/repo#5", [], ["other/repo#5"]),
        ("Colour #ff0000 and step #1.", [1], []),
        ("no reference here", [], []),
        ("#7 #7 #8", [7, 8], []),
    ],
)
def test_references_tell_this_repository_from_others(text, inside, outside):
    assert references(text, REPO) == (inside, outside)


def test_parse_timeline_drops_comment_text_and_names():
    raw = [
        {
            "event": "commented",
            "created_at": "2026-01-02T00:00:00Z",
            "body": "Duplicate of #5, thanks @someone",
            "user": {"login": "maintainer", "type": "User"},
        },
        {
            "event": "labeled",
            "created_at": "2026-01-02T00:00:01Z",
            "label": {"name": "topic:gui"},
            "actor": {"login": "a-bot", "type": "Bot"},
        },
        {
            "event": "closed",
            "created_at": "2026-01-02T00:00:02Z",
            "state_reason": "duplicate",
            "actor": {"login": "maintainer", "type": "User"},
        },
        {
            "event": "renamed",
            "created_at": "2026-01-02T00:00:03Z",
            "rename": {"from": "old title", "to": "new title"},
        },
        {"event": "subscribed", "created_at": "2026-01-02T00:00:04Z"},
        {
            "event": "cross-referenced",
            "created_at": "2026-01-02T00:00:05Z",
            "source": {"issue": {"number": 99}},
        },
    ]
    events = parse_timeline(raw, REPO)
    assert [e.event for e in events] == [
        "commented",
        "labeled",
        "closed",
        "renamed",
        "cross-referenced",
    ]
    comment, labeled, closed, renamed, crossref = events
    assert comment.refs == [5] and comment.says_duplicate and comment.actor_type == "User"
    assert labeled.label == "topic:gui" and labeled.actor_type == "Bot"
    assert closed.state_reason == "duplicate"
    assert renamed.old_title == "old title"
    assert crossref.refs == [99]
    dumped = " ".join(e.model_dump_json() for e in events)
    assert "maintainer" not in dumped and "thanks" not in dumped and "a-bot" not in dumped


def test_parse_timeline_fails_loudly_on_a_changed_shape():
    with pytest.raises(FeedError, match="timeline no longer parses"):
        parse_timeline([{"event": "labeled", "label": None}], REPO)


def test_search_returns_issues_oldest_first_and_pages(fake, client):
    fake.issues = [make_issue(n, f"issue {n}", day=n / 100) for n in range(1, 251)]
    found = list(client.issues_created(T0, T0 + timedelta(days=30)))
    assert [i.number for i in found] == list(range(1, 251))
    assert client.requests["search"] == 3


def test_search_skips_pull_requests_if_one_slips_in(fake, client):
    fake.issues = [make_issue(1, "an issue")]
    original = fake._search

    def with_pull_request(params):
        response = original(params)
        body = response.json()
        body["items"].append({**api_item(make_issue(2, "a pull request")), "pull_request": {}})
        return httpx.Response(200, json=body)

    fake._search = with_pull_request
    assert [i.number for i in client.issues_created(T0, T0 + timedelta(days=1))] == [1]


def test_a_window_above_the_search_limit_is_split(fake, client):
    fake.issues = [make_issue(n, f"issue {n}", day=n / 100) for n in range(1, 1201)]
    found = list(client.issues_created(T0, T0 + timedelta(days=30)))
    assert len(found) == 1200
    assert len({i.number for i in found}) == 1200
    windows = {params["q"] for path, params in fake.calls if path == "/search/issues"}
    assert len(windows) >= 3


def test_incomplete_results_are_retried_then_fail(fake, client):
    body = {"total_count": 1, "incomplete_results": True, "items": []}
    fake.fail_next = [httpx.Response(200, json=body) for _ in range(3)]
    with pytest.raises(FeedError, match="incomplete"):
        list(client.issues_created(T0, T0 + timedelta(days=1)))


def test_a_search_response_without_items_is_a_feed_error(fake, client):
    fake.fail_next = [httpx.Response(200, json={"message": "changed"})]
    with pytest.raises(FeedError, match="no longer parses"):
        client.search_page("repo:x is:issue")


def test_server_errors_are_retried(fake):
    waits = []
    client = make_client(fake, sleep=waits.append)
    fake.issues = [make_issue(1, "t")]
    fake.fail_next = [httpx.Response(502), httpx.Response(503)]
    assert len(list(client.issues_created(T0, T0 + timedelta(days=1)))) == 1
    assert waits == [1.0, 2.0]


def test_a_client_error_is_not_retried(fake, client):
    fake.fail_next = [httpx.Response(422, json={"message": "Validation Failed"})]
    with pytest.raises(FeedError, match="HTTP 422"):
        client.search_page("bad query")
    assert len(fake.calls) == 1


def test_an_exhausted_quota_waits_for_the_reset(fake):
    waits = []
    client = make_client(fake, sleep=waits.append, clock=lambda: 1000.0)
    fake.issues = [make_issue(1, "t")]
    fake.fail_next = [
        httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1030"})
    ]
    assert len(list(client.issues_created(T0, T0 + timedelta(days=1)))) == 1
    assert waits == [32.0]


def test_a_long_wait_is_refused_when_the_caller_sets_a_bound(fake):
    client = make_client(fake, clock=lambda: 1000.0, max_wait_s=60)
    fake.fail_next = [
        httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "4600"})
    ]
    with pytest.raises(RateLimited) as caught:
        client.timeline(5)
    assert caught.value.reset_at is not None


def test_retry_after_is_honoured(fake):
    waits = []
    client = make_client(fake, sleep=waits.append)
    fake.issues = [make_issue(1, "t")]
    fake.fail_next = [httpx.Response(429, headers={"retry-after": "20"})]
    list(client.issues_created(T0, T0 + timedelta(days=1)))
    assert waits == [21.0]


def test_requests_are_paced(fake):
    now = [0.0]
    waits = []

    def sleep(seconds):
        waits.append(round(seconds, 2))
        now[0] += seconds

    client = make_client(fake, pace=True, sleep=sleep, clock=lambda: now[0])
    fake.issues = [make_issue(n, "t", day=n / 1000) for n in range(1, 151)]
    list(client.issues_created(T0, T0 + timedelta(days=1)))
    assert waits == [6.2]


def test_a_token_is_sent_only_when_given(fake):
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return fake.handler(request)

    from triagedesk.feed import GitHubClient

    for token in (None, "abc"):
        client = GitHubClient(REPO, token=token, transport=httpx.MockTransport(handler), pace=False)
        client.search_page(client.window_query(T0, T0))
    assert seen == [None, "Bearer abc"]


def test_labels_and_timeline_use_the_core_quota(fake, client):
    fake.labels = [
        {"name": "topic:gui", "description": None},
        {"name": "bug", "description": "Broken"},
    ]
    fake.timelines = {
        5: [{"event": "closed", "state_reason": "duplicate", "created_at": "2026-01-02T00:00:00Z"}]
    }
    labels = client.labels()
    assert [(i.name, i.description) for i in labels] == [("topic:gui", ""), ("bug", "Broken")]
    assert client.timeline(5)[0].state_reason == "duplicate"
    assert client.requests == {"search": 0, "core": 2}


def test_rate_limit_reports_remaining_and_total(client):
    assert client.rate_limit() == {"core": (55, 60), "search": (9, 10)}


def test_month_ranges_cover_the_span_without_overlap():
    months = list(month_ranges(date(2025, 11, 15), date(2026, 2, 10)))
    assert months == [
        (date(2025, 11, 15), date(2025, 11, 30)),
        (date(2025, 12, 1), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 10)),
    ]


def test_backfill_stores_issues_and_skips_finished_months(store):
    fake = FakeGitHub([make_issue(n, f"issue {n}", day=n) for n in range(1, 61)])
    client = make_client(fake)
    first = fetch_range(client, store, date(2026, 1, 1), date(2026, 2, 28), today=date(2026, 6, 1))
    assert first == {"months": 2, "skipped": 0, "fetched": 58, "new": 58}
    calls = len(fake.calls)
    second = fetch_range(client, store, date(2026, 1, 1), date(2026, 2, 28), today=date(2026, 6, 1))
    assert second["skipped"] == 2 and second["fetched"] == 0
    assert len(fake.calls) == calls


def test_backfill_fetches_the_running_month_again(store):
    fake = FakeGitHub([make_issue(1, "t", day=3)])
    client = make_client(fake)
    for _ in range(2):
        result = fetch_range(
            client, store, date(2026, 1, 1), date(2026, 1, 31), today=date(2026, 1, 20)
        )
        assert result["fetched"] == 1
    assert store.count() == 1
