"""Client for the TED search API: one polite request at a time, paged to the end.

TED is the EU's public procurement journal. The search API needs no key. This module
only reads. It never sends more than one request at a time and waits between requests.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import date

import httpx

SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"
PAGE_SIZE = 250
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

# Fields asked for. `links` is left out on purpose: it is 4.5 KB per notice and the
# notice URL can be built from the publication number.
COMPETITION_FIELDS: tuple[str, ...] = (
    "publication-number",
    "publication-date",
    "notice-type",
    "procedure-identifier",
    "deadline-receipt-tender-date-lot",
    "classification-cpv",
    "buyer-country",
    "buyer-name",
    "title-proc",
    "description-proc",
    "title-lot",
    "description-lot",
    "estimated-value-proc",
    "estimated-value-cur-proc",
    "procedure-type",
    "contract-nature-main-proc",
    "official-language",
    "selection-criterion-description-lot",
)
# No contact fields: winner e-mail, phone and address exist in the feed and are not fetched.
AWARD_FIELDS: tuple[str, ...] = (
    "publication-number",
    "publication-date",
    "notice-type",
    "procedure-identifier",
    "winner-name",
    "winner-identifier",
    "winner-country",
    "winner-size",
    "classification-cpv",
    "buyer-country",
    "buyer-name",
    "title-proc",
    "description-proc",
    "result-value-notice",
    "result-value-cur-notice",
)
COMPETITION_TYPE = "cn-standard"
AWARD_TYPE = "can-standard"


class FeedError(RuntimeError):
    """The feed did not answer in the shape this client understands."""


def _scope(divisions: tuple[str, ...]) -> str:
    return "(" + " OR ".join(f"classification-cpv={d}*" for d in divisions) + ")"


def _dates(start: date, end: date) -> str:
    return f"publication-date>={start:%Y%m%d} AND publication-date<={end:%Y%m%d}"


def competition_query(divisions: tuple[str, ...], start: date, end: date) -> str:
    return f"notice-type={COMPETITION_TYPE} AND {_scope(divisions)} AND {_dates(start, end)}"


def award_query(divisions: tuple[str, ...], start: date, end: date) -> str:
    return f"notice-type={AWARD_TYPE} AND {_scope(divisions)} AND {_dates(start, end)}"


class TedClient:
    def __init__(
        self,
        *,
        pause_s: float = 1.2,
        user_agent: str = "tender-radar/0.1",
        max_attempts: int = 5,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._pause_s = pause_s
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self._http = httpx.Client(
            transport=transport,
            timeout=90.0,
            headers={"User-Agent": user_agent, "Content-Type": "application/json"},
        )
        self.requests_made = 0

    def close(self) -> None:
        self._http.close()

    def _wait_turn(self) -> None:
        if self._last_request is not None:
            remaining = self._pause_s - (self._clock() - self._last_request)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request = self._clock()

    def _post(self, body: dict) -> dict:
        last_error = "no attempt made"
        for attempt in range(1, self._max_attempts + 1):
            self._wait_turn()
            self.requests_made += 1
            try:
                response = self._http.post(SEARCH_URL, json=body)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code == 200:
                    return self._decode(response)
                last_error = f"HTTP {response.status_code}"
                if response.status_code not in RETRY_STATUS:
                    raise FeedError(f"TED answered {last_error}: {response.text[:200]!r}")
            if attempt < self._max_attempts:
                self._sleep(min(60.0, 5.0 * attempt))
        raise FeedError(f"TED did not answer after {self._max_attempts} attempts ({last_error}).")

    @staticmethod
    def _decode(response: httpx.Response) -> dict:
        try:
            payload = response.json()
        except ValueError as exc:
            raise FeedError(
                f"TED answered 200 with a body that is not JSON: {response.text[:120]!r}"
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("notices"), list):
            raise FeedError("TED answered without a 'notices' list; the response format changed.")
        return payload

    def count(self, query: str) -> int:
        payload = self._post({"query": query, "fields": ["publication-number"], "limit": 1})
        total = payload.get("totalNoticeCount")
        if not isinstance(total, int):
            raise FeedError("TED answered without 'totalNoticeCount'.")
        return total

    def search(self, query: str, fields: tuple[str, ...]) -> Iterator[dict]:
        """Every notice matching `query`, following the iteration token to the end."""
        token: str | None = None
        while True:
            body: dict = {
                "query": query,
                "fields": list(fields),
                "limit": PAGE_SIZE,
                "paginationMode": "ITERATION",
            }
            if token:
                body["iterationNextToken"] = token
            payload = self._post(body)
            notices = payload["notices"]
            yield from notices
            token = payload.get("iterationNextToken")
            if not notices or not token:
                return
