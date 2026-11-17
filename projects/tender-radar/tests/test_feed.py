from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from tenderradar.backfill import fetch_range, month_ranges
from tenderradar.feed import (
    AWARD_FIELDS,
    COMPETITION_FIELDS,
    FeedError,
    TedClient,
    award_query,
    competition_query,
)
from tenderradar.store import COMPETITION, Store


def client_for(handler, **kwargs) -> tuple[TedClient, list[float]]:
    sleeps: list[float] = []
    client = TedClient(
        transport=httpx.MockTransport(handler), sleep=sleeps.append, clock=lambda: 0.0, **kwargs
    )
    return client, sleeps


def page(notices, token=None, total=None):
    return httpx.Response(
        200,
        json={
            "notices": notices,
            "iterationNextToken": token,
            "totalNoticeCount": total if total is not None else len(notices),
        },
    )


def test_queries_name_type_scope_and_dates():
    query = competition_query(("33", "38"), date(2026, 4, 1), date(2026, 4, 30))
    assert query == (
        "notice-type=cn-standard AND (classification-cpv=33* OR classification-cpv=38*) "
        "AND publication-date>=20260401 AND publication-date<=20260430"
    )
    assert award_query(("24",), date(2026, 1, 1), date(2026, 1, 2)).startswith(
        "notice-type=can-standard AND (classification-cpv=24*)"
    )


def test_no_contact_or_link_fields_are_requested():
    requested = set(COMPETITION_FIELDS) | set(AWARD_FIELDS)
    assert "links" not in requested
    assert not {f for f in requested if "email" in f or "tel" in f or "contact" in f}


def test_search_follows_the_token_until_the_feed_runs_out():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "iterationNextToken" not in body:
            return page([{"publication-number": "1"}, {"publication-number": "2"}], token="t1")
        if body["iterationNextToken"] == "t1":
            return page([{"publication-number": "3"}], token="t2")
        return page([], token=None)

    client, _ = client_for(handler)
    numbers = [n["publication-number"] for n in client.search("q", ("publication-number",))]
    assert numbers == ["1", "2", "3"]
    assert [b.get("iterationNextToken") for b in bodies] == [None, "t1", "t2"]
    assert all(b["limit"] == 250 and b["paginationMode"] == "ITERATION" for b in bodies)


def test_requests_are_spaced():
    def handler(request):
        return page([], total=7)

    client, sleeps = client_for(handler, pause_s=1.2)
    client.count("q")
    client.count("q")
    # The clock never advances in this test, so the second request waits the full pause.
    assert sleeps == [1.2]


def test_a_refused_request_is_retried_then_succeeds():
    answers = iter([httpx.Response(429), httpx.Response(503), page([], total=4)])

    client, sleeps = client_for(lambda request: next(answers))
    assert client.count("q") == 4
    assert client.requests_made == 3
    assert 5.0 in sleeps and 10.0 in sleeps


def test_gives_up_with_a_named_error_after_the_last_attempt():
    client, _ = client_for(lambda request: httpx.Response(503), max_attempts=3)
    with pytest.raises(FeedError, match="after 3 attempts"):
        client.count("q")
    assert client.requests_made == 3


def test_a_bad_query_is_not_retried():
    client, _ = client_for(lambda request: httpx.Response(400, text="bad field"))
    with pytest.raises(FeedError, match="HTTP 400"):
        client.count("q")
    assert client.requests_made == 1


def test_a_page_that_is_not_json_is_an_error_not_an_empty_day():
    client, _ = client_for(lambda request: httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(FeedError, match="not JSON"):
        list(client.search("q", ("publication-number",)))


def test_a_changed_response_shape_is_an_error():
    client, _ = client_for(lambda request: httpx.Response(200, json={"results": []}))
    with pytest.raises(FeedError, match="format changed"):
        list(client.search("q", ("publication-number",)))


def test_a_dropped_connection_is_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("reset")
        return page([], total=1)

    client, _ = client_for(handler)
    assert client.count("q") == 1


def test_month_ranges_cut_at_month_ends():
    assert month_ranges(date(2026, 1, 20), date(2026, 3, 5)) == [
        (date(2026, 1, 20), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 5)),
    ]


def test_backfill_skips_a_finished_month_but_refetches_the_current_one():
    queries = []

    def handler(request):
        queries.append(json.loads(request.content)["query"])
        return page(
            [
                {
                    "publication-number": f"{len(queries)}-2026",
                    "publication-date": "2026-03-10+01:00",
                    "procedure-identifier": "p",
                }
            ]
        )

    client, _ = client_for(handler)
    store = Store(":memory:")
    args = (client, store, COMPETITION, ("38",), date(2026, 3, 1), date(2026, 4, 14))
    assert fetch_range(*args, today=date(2026, 4, 14)) == 2
    assert fetch_range(*args, today=date(2026, 4, 14)) == 1
    # March was complete and is remembered; April is still running and is asked again.
    assert sum("20260301" in q for q in queries) == 1
    assert sum("20260401" in q for q in queries) == 2
