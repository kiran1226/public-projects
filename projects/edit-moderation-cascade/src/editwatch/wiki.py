"""Wikimedia's APIs, read-only: the recent-changes list, revision text and tags, and
Wikimedia's own revert-risk model.

Measured on 2026-11-17 with a User-Agent that names the client and no contact: the
wiki API refused the eleventh request of a burst (HTTP 429, Retry-After 35) and
accepted one request every five seconds. So requests are made one at a time, paced,
with `maxlag`, and revisions are read 50 to a request. Nothing here writes to a wiki.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from .models import Edit
from .scrub import scrub_comment

RISK_URL = (
    "https://api.wikimedia.org/service/lw/inference/v1/models/revertrisk-language-agnostic:predict"
)
REVERTED_TAG = "mw-reverted"
BATCH = 50


class FeedError(RuntimeError):
    """The feed answered in a way the desk cannot use. The run fails; nothing is guessed."""


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_recent_change(item: dict) -> tuple[Edit, list[str]]:
    """One entry of the recent-changes list as an edit and its tags. No account name is read."""
    try:
        edit = Edit(
            rev_id=item["revid"],
            parent_id=item["old_revid"],
            title=item["title"],
            timestamp=_when(item["timestamp"]),
            minor=bool(item.get("minor")),
            bot=bool(item.get("bot")),
            temp=bool(item.get("temp")) or bool(item.get("anon")),
            old_len=item.get("oldlen") or 0,
            new_len=item.get("newlen") or 0,
            comment=scrub_comment(item.get("comment") or ""),
        )
        return edit, list(item.get("tags") or [])
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise FeedError(f"A recent-changes entry no longer parses: {exc}") from exc


class WikiClient:
    """Paced, serial, read-only requests with typed results."""

    def __init__(
        self,
        host: str = "en.wikipedia.org",
        *,
        user_agent: str,
        pause_s: float = 5.0,
        risk_pause_s: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        max_wait_s: float = 120.0,
    ):
        self.api = f"https://{host}/w/api.php"
        self._http = httpx.Client(
            headers={"User-Agent": user_agent}, timeout=60, transport=transport
        )
        self._pause = {"wiki": pause_s, "risk": risk_pause_s}
        self._last: dict[str, float] = {}
        self._sleep = sleep
        self._clock = clock
        self._max_wait_s = max_wait_s
        self.requests = {"wiki": 0, "risk": 0}
        # Seconds each successful request took, for the load test.
        self.latencies: dict[str, list[float]] = {"wiki": [], "risk": []}

    def close(self) -> None:
        self._http.close()

    def _wait_turn(self, kind: str) -> None:
        if kind in self._last:
            due = self._last[kind] + self._pause[kind]
            now = self._clock()
            if due > now:
                self._sleep(due - now)

    def _request(self, kind: str, method: str, url: str, **kwargs) -> dict:
        last = "no response"
        for attempt in range(5):
            self._wait_turn(kind)
            started = self._clock()
            try:
                response = self._http.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last = type(exc).__name__
                self._last[kind] = self._clock()
                self._sleep(min(60.0, 2.0**attempt))
                continue
            self._last[kind] = self._clock()
            self.requests[kind] += 1
            if response.status_code == 429 or response.status_code >= 500:
                retry_after = response.headers.get("retry-after", "")
                wait = float(retry_after) + 1 if retry_after.isdigit() else 30.0 * (attempt + 1)
                if wait > self._max_wait_s:
                    raise FeedError(
                        f"The next attempt would wait {wait:.0f} seconds after HTTP "
                        f"{response.status_code}; stopping instead of pressing on."
                    )
                last = f"HTTP {response.status_code}"
                self._sleep(wait)
                continue
            if response.status_code != 200:
                raise FeedError(f"Wikimedia answered HTTP {response.status_code} for {url}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise FeedError(f"A response from {url} is not JSON") from exc
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict) and error.get("code") == "maxlag":
                # The servers are behind. Wait as asked and try again.
                last = "maxlag"
                self._sleep(5.0)
                continue
            if error:
                raise FeedError(f"Wikimedia API error: {error}")
            self.latencies[kind].append(self._clock() - started)
            return payload
        raise FeedError(f"Wikimedia did not answer after five attempts ({last})")

    def _query(self, **params) -> dict:
        params.update(action="query", format="json", formatversion="2", maxlag="5")
        return self._request("wiki", "GET", self.api, params=params)

    # -- endpoints -----------------------------------------------------------------

    def recent_changes(
        self, start: datetime, limit: int = 500, namespace: int = 0
    ) -> list[tuple[Edit, list[str]]]:
        """Up to `limit` article edits made from `start` on, oldest first, with their tags."""
        payload = self._query(
            list="recentchanges",
            rcnamespace=str(namespace),
            rctype="edit",
            rclimit=str(limit),
            rcdir="newer",
            rcstart=start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            rcprop="ids|title|timestamp|flags|sizes|comment|tags",
        )
        try:
            items = payload["query"]["recentchanges"]
        except (KeyError, TypeError) as exc:
            raise FeedError("The recent-changes response no longer parses.") from exc
        return [parse_recent_change(item) for item in items]

    def _revisions(self, rev_ids: list[int], props: str) -> list[dict]:
        if len(rev_ids) > BATCH:
            raise ValueError(f"At most {BATCH} revisions a request.")
        payload = self._query(
            prop="revisions", revids="|".join(str(r) for r in rev_ids), rvprop=props,
            rvslots="main",
        )  # fmt: skip
        try:
            pages = payload["query"].get("pages", [])
            return [rev for page in pages for rev in page.get("revisions", [])]
        except (KeyError, TypeError, AttributeError) as exc:
            raise FeedError("A revisions response no longer parses.") from exc

    def revision_texts(self, rev_ids: list[int]) -> dict[int, str | None]:
        """The wikitext of each revision. None where it is hidden, deleted or missing."""
        found: dict[int, str | None] = dict.fromkeys(rev_ids)
        for rev in self._revisions(rev_ids, "ids|content"):
            slot = (rev.get("slots") or {}).get("main") or {}
            if "content" in slot:
                found[rev["revid"]] = slot["content"]
        return found

    def revision_tags(self, rev_ids: list[int]) -> dict[int, list[str] | None]:
        """The tags on each revision now. None where the revision can no longer be read."""
        found: dict[int, list[str] | None] = dict.fromkeys(rev_ids)
        for rev in self._revisions(rev_ids, "ids|tags"):
            found[rev["revid"]] = list(rev.get("tags") or [])
        return found

    def revert_risk(self, rev_id: int, lang: str = "en") -> float | None:
        """Wikimedia's own model: the probability that this revision is reverted."""
        try:
            payload = self._request("risk", "POST", RISK_URL, json={"rev_id": rev_id, "lang": lang})
        except FeedError as exc:
            # The model cannot score some revisions (no parent, deleted). Not a feed fault.
            if "HTTP 4" in str(exc) and "HTTP 429" not in str(exc):
                return None
            raise
        try:
            return float(payload["output"]["probabilities"]["true"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FeedError("The revert-risk response no longer parses.") from exc
