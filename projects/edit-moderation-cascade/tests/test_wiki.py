"""The wiki client: what it parses, how it paces itself, and when it stops."""

from __future__ import annotations

import httpx
import pytest

from editwatch.models import Edit
from editwatch.wiki import REVERTED_TAG, RISK_URL, FeedError, parse_recent_change

from conftest import ACCOUNT, USER_AGENT, api_item, at, make_client, make_edit


def test_a_recent_change_keeps_ids_sizes_flags_summary_and_tags():
    edit = make_edit(
        812, parent_id=790, title="Example Lighthouse", minute=90, minor=True, old_len=5200,
        new_len=4100, comment="/* Keepers */ trimmed the list",
    )  # fmt: skip
    parsed, tags = parse_recent_change(api_item(edit, tags=["mobile edit", REVERTED_TAG]))
    assert parsed == edit
    assert parsed.delta == -1100
    assert parsed.timestamp == at(90)
    assert tags == ["mobile edit", REVERTED_TAG]


def test_a_recent_change_is_stored_with_its_summary_scrubbed():
    item = api_item(make_edit(5), comment=f"Undid revision 4 by {ACCOUNT} (talk): not sourced")
    parsed, _ = parse_recent_change(item)
    assert parsed.comment == "Undid revision 4 by [name]: not sourced"


def test_a_recent_change_reads_no_account_name():
    item = api_item(make_edit(5), comment=f"thanks @{ACCOUNT}")
    assert item["user"] == ACCOUNT
    parsed, tags = parse_recent_change(item)
    assert ACCOUNT not in parsed.model_dump_json() + " ".join(tags)
    assert not {"user", "userid", "account", "name"} & set(Edit.model_fields)


@pytest.mark.parametrize(
    ("flags", "temporary"),
    [
        ({"temp": True}, True),
        ({"anon": True}, True),
        ({"temp": False, "anon": False}, False),
        ({}, False),
    ],
)
def test_a_temp_or_anon_flag_marks_a_temporary_account(flags, temporary):
    parsed, _ = parse_recent_change(api_item(make_edit(5), **flags))
    assert parsed.temp is temporary


@pytest.mark.parametrize("missing", ["revid", "old_revid", "title", "timestamp"])
def test_a_recent_change_fails_loudly_when_a_required_field_is_gone(missing):
    item = api_item(make_edit(5))
    del item[missing]
    with pytest.raises(FeedError, match="no longer parses"):
        parse_recent_change(item)


def test_a_recent_change_with_a_broken_timestamp_fails_loudly():
    with pytest.raises(FeedError, match="no longer parses"):
        parse_recent_change(api_item(make_edit(5), timestamp="the day before yesterday"))


def test_recent_changes_are_read_oldest_first_from_the_start_up_to_the_limit(fake, client):
    fake.changes = [
        api_item(make_edit(n, minute=n), tags=["tag"] * (n % 2)) for n in (4, 2, 5, 1, 3)
    ]
    fake.changes.append(api_item(make_edit(9, minute=2.5), ns=1))
    found = client.recent_changes(at(2), limit=3)
    assert [edit.rev_id for edit, _ in found] == [2, 3, 4]
    assert [tags for _, tags in found] == [[], ["tag"], []]
    (params,) = fake.asked("recentchanges")
    assert params["rcstart"] == "2026-11-01T00:02:00Z"
    assert (params["rcdir"], params["rctype"], params["rcnamespace"]) == ("newer", "edit", "0")


def test_a_changed_recent_changes_answer_fails_loudly(fake, client):
    fake.fail_next = [httpx.Response(200, json={"batchcomplete": True, "continue": {}})]
    with pytest.raises(FeedError, match="recent-changes response no longer parses"):
        client.recent_changes(at(0))


def test_every_request_names_the_client_and_sets_maxlag(fake, client):
    fake.texts = {1: "a line"}
    fake.risks = {1: 0.5}
    client.recent_changes(at(0))
    client.revision_texts([1])
    client.revert_risk(1)
    assert [r.headers["user-agent"] for r in fake.requests] == [USER_AGENT] * 3
    assert [r.url.params["maxlag"] for r in fake.requests[:2]] == ["5", "5"]
    assert str(fake.requests[2].url) == RISK_URL


def test_the_client_never_asks_for_account_names(fake, client):
    fake.texts = {1: "a line"}
    client.recent_changes(at(0))
    client.revision_texts([1])
    client.revision_tags([1])
    asked = [params.get("rcprop", "") + params.get("rvprop", "") for _, params in fake.calls]
    assert len(asked) == 3
    assert not any("user" in props for props in asked)


def test_the_first_request_goes_at_once_and_the_next_waits_the_pause(fake, clock):
    client = make_client(fake, clock=clock, pause_s=5.0)
    client.revision_tags([1])
    assert clock.sleeps == []
    client.revision_tags([2])
    client.revision_tags([3])
    assert clock.sleeps == [5.0, 5.0]


def test_time_that_already_passed_counts_towards_the_pause(fake, clock):
    client = make_client(fake, clock=clock, pause_s=5.0)
    client.revision_tags([1])
    clock.advance(2.0)
    client.revision_tags([2])
    clock.advance(60.0)
    client.revision_tags([3])
    assert clock.sleeps == [3.0]


def test_requests_are_serial_the_pause_runs_from_the_last_answer(fake, clock):
    in_flight = []
    fake.on_request = lambda request: (in_flight.append(clock()), clock.advance(2.0))
    client = make_client(fake, clock=clock, pause_s=5.0)
    for rev_id in (1, 2, 3):
        client.revision_tags([rev_id])
    # Each request takes two seconds; the next one starts five seconds after its answer.
    assert in_flight == [0.0, 7.0, 14.0]
    assert clock.sleeps == [5.0, 5.0]


def test_the_revert_risk_service_is_paced_on_its_own(fake, clock):
    fake.risks = {1: 0.2, 2: 0.4}
    client = make_client(fake, clock=clock, pause_s=5.0, risk_pause_s=1.0)
    client.revision_tags([1])
    client.revert_risk(1)
    assert clock.sleeps == []
    client.revert_risk(2)
    assert clock.sleeps == [1.0]
    assert client.requests == {"wiki": 1, "risk": 2}


def test_http_429_waits_retry_after_plus_a_second_and_tries_again(fake, clock, client):
    fake.tags = {1: ["tag"]}
    fake.fail_next = [httpx.Response(429, headers={"Retry-After": "35"})]
    assert client.revision_tags([1]) == {1: ["tag"]}
    assert clock.sleeps == [36.0]
    assert len(fake.requests) == 2


def test_a_wait_longer_than_the_limit_stops_the_run(fake, clock):
    client = make_client(fake, clock=clock, max_wait_s=120.0)
    fake.fail_next = [httpx.Response(429, headers={"Retry-After": "300"})]
    with pytest.raises(FeedError, match="would wait 301 seconds"):
        client.revision_tags([1])
    assert clock.sleeps == []
    assert len(fake.requests) == 1


def test_a_maxlag_answer_is_retried_after_a_pause(fake, clock, client):
    fake.tags = {1: []}
    lagging = {
        "error": {"code": "maxlag", "info": "Waiting for a database server: 6 seconds lagged."}
    }
    fake.fail_next = [httpx.Response(200, json=lagging), httpx.Response(200, json=lagging)]
    assert client.revision_tags([1]) == {1: []}
    assert clock.sleeps == [5.0, 5.0]
    assert len(fake.requests) == 3


def test_another_api_error_stops_the_run(fake, client):
    fake.fail_next = [
        httpx.Response(200, json={"error": {"code": "badvalue", "info": "Unrecognized value"}})
    ]
    with pytest.raises(FeedError, match="Wikimedia API error.*badvalue"):
        client.revision_tags([1])
    assert len(fake.requests) == 1


def test_http_500_is_retried(fake, clock, client):
    fake.tags = {1: []}
    fake.fail_next = [httpx.Response(500), httpx.Response(503)]
    assert client.revision_tags([1]) == {1: []}
    assert clock.sleeps == [30.0, 60.0]


def test_a_server_that_keeps_failing_is_given_up_on_after_five_attempts(fake, clock):
    client = make_client(fake, clock=clock, max_wait_s=1000.0)
    fake.fail_next = [httpx.Response(500) for _ in range(5)]
    with pytest.raises(FeedError, match=r"five attempts \(HTTP 500\)"):
        client.revision_tags([1])
    assert len(fake.requests) == 5


def test_http_404_is_not_retried(fake, client):
    fake.fail_next = [httpx.Response(404)]
    with pytest.raises(FeedError, match="HTTP 404"):
        client.revision_tags([1])
    assert len(fake.requests) == 1


def test_a_network_error_is_retried(fake, clock, client):
    fake.tags = {1: []}
    failures = [httpx.ConnectError("connection refused")]

    def fail_once(request):
        if failures:
            raise failures.pop()

    fake.on_request = fail_once
    assert client.revision_tags([1]) == {1: []}
    assert clock.sleeps[0] == 1.0
    assert len(fake.requests) == 2


def test_an_answer_that_is_not_json_stops_the_run(fake, client):
    fake.fail_next = [httpx.Response(200, text="<html>maintenance</html>")]
    with pytest.raises(FeedError, match="not JSON"):
        client.revision_tags([1])


def test_revision_texts_are_none_where_the_wiki_returns_no_text(fake, client):
    fake.texts = {1: "first line\nsecond line", 2: "", 3: "hidden from view"}
    fake.hidden = {3}
    assert client.revision_texts([1, 2, 3, 4]) == {
        1: "first line\nsecond line",
        2: "",
        3: None,
        4: None,
    }
    assert fake.revids_asked() == [[1, 2, 3, 4]]


def test_revision_tags_are_none_for_a_revision_that_is_gone(fake, client):
    fake.tags = {1: [REVERTED_TAG, "mobile edit"], 2: []}
    assert client.revision_tags([1, 2, 3]) == {1: [REVERTED_TAG, "mobile edit"], 2: [], 3: None}


@pytest.mark.parametrize("method", ["revision_texts", "revision_tags"])
def test_more_than_fifty_revisions_in_one_request_are_refused(fake, client, method):
    fake.tags = {n: [] for n in range(1, 52)}
    assert len(getattr(client, method)(list(range(1, 51)))) == 50
    with pytest.raises(ValueError, match="At most 50"):
        getattr(client, method)(list(range(1, 52)))
    assert len(fake.requests) == 1


def test_revert_risk_is_the_probability_the_service_gives(fake, client):
    fake.risks = {77: 0.8125}
    assert client.revert_risk(77) == 0.8125
    assert fake.calls == [("risk", {"rev_id": 77, "lang": "en"})]
    assert fake.requests[0].method == "POST"


def test_revert_risk_is_none_for_a_revision_the_service_cannot_score(fake, client):
    fake.risks = {78: 0.1}
    assert client.revert_risk(77) is None
    assert client.revert_risk(78) == 0.1
    assert len(fake.requests) == 2


def test_revert_risk_fails_loudly_on_a_changed_answer(fake, client):
    fake.fail_next = [httpx.Response(200, json={"output": {"score": 0.4}})]
    with pytest.raises(FeedError, match="revert-risk response no longer parses"):
        client.revert_risk(77)


def test_revert_risk_does_not_swallow_a_rate_limit_refusal(fake, client):
    fake.fail_next = [httpx.Response(429, headers={"Retry-After": "600"})]
    with pytest.raises(FeedError, match="would wait 601 seconds"):
        client.revert_risk(77)


def test_request_counts_and_latencies_are_kept_per_kind(fake, clock, client):
    fake.risks = {1: 0.3}
    fake.on_request = lambda request: clock.advance(2.0)
    client.revision_tags([1])
    client.revert_risk(1)
    client.revert_risk(2)  # refused with HTTP 400: counted as a request, no latency
    assert client.requests == {"wiki": 1, "risk": 2}
    assert client.latencies == {"wiki": [2.0], "risk": [2.0]}
