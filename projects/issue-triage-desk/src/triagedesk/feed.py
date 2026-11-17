"""GitHub's REST API, read-only: issue search, the label list and an issue's timeline.

Three things shaped this file, all measured on 2026-11-17 without a token:

- The issue list endpoint mixes in pull requests (two thirds of each page). The search
  endpoint returns issues only, 100 per request, and has its own quota of 10 requests
  a minute. So history and the day's new issues both come from search.
- A search returns at most 1,000 results, so every query is a window of creation time.
- Timelines cost one request each against 60 an hour without a token. Only issues
  closed as duplicates need one.

A token (GITHUB_TOKEN) raises the limits and is never required. Fields that name a
person are not read.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from .models import Issue, LabelInfo, TimelineEvent

API = "https://api.github.com"
SEARCH_LIMIT = 1000
PER_PAGE = 100
# Seconds between requests: search allows 10 a minute without a token, 30 with one.
SEARCH_PAUSE = {False: 6.2, True: 2.1}
CORE_PAUSE = 0.25

# Timeline entries truth can use. Everything else (subscriptions, mentions) is skipped.
KEPT_EVENTS = frozenset(
    {
        "commented",
        "closed",
        "reopened",
        "labeled",
        "unlabeled",
        "renamed",
        "marked_as_duplicate",
        "unmarked_as_duplicate",
        "cross-referenced",
    }
)

_DUPLICATE_WORD = re.compile(r"\b(duplicate|duplicates|dupe|dup)\b", re.IGNORECASE)
_URL_REF = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)")
_HASH_REF = re.compile(r"(?<![\w/])#(\d+)\b")
_CROSS_REF = re.compile(r"\b([\w.-]+/[\w.-]+)#(\d+)\b")


class FeedError(RuntimeError):
    """The feed answered in a way the desk cannot use. The run fails; nothing is guessed."""


class RateLimited(FeedError):
    """The quota is used up and the wait would be longer than the caller allows."""

    def __init__(self, message: str, reset_at: datetime | None = None):
        super().__init__(message)
        self.reset_at = reset_at


def _when(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_issue(item: dict) -> Issue:
    """One search or list item as a typed record. Raises FeedError if it does not fit."""
    try:
        return Issue(
            number=item["number"],
            title=item["title"],
            body=item.get("body") or "",
            created_at=_when(item["created_at"]),
            updated_at=_when(item.get("updated_at")),
            closed_at=_when(item.get("closed_at")),
            state=item["state"],
            state_reason=item.get("state_reason"),
            labels=sorted(label["name"] for label in item.get("labels") or []),
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise FeedError(f"An issue record no longer parses: {type(exc).__name__}: {exc}") from exc


def references(text: str, repo: str) -> tuple[list[int], list[str]]:
    """Issue numbers a comment refers to: in this repository, and in others."""
    inside: list[int] = []
    outside: list[str] = []
    for owner_repo, number in _URL_REF.findall(text):
        if owner_repo.lower() == repo.lower():
            inside.append(int(number))
        else:
            outside.append(f"{owner_repo}#{number}")
    for owner_repo, number in _CROSS_REF.findall(text):
        if owner_repo.lower() == repo.lower():
            inside.append(int(number))
        else:
            outside.append(f"{owner_repo}#{number}")
    inside.extend(int(number) for number in _HASH_REF.findall(text))
    return list(dict.fromkeys(inside)), list(dict.fromkeys(outside))


def parse_timeline(items: list[dict], repo: str) -> list[TimelineEvent]:
    """Reduce a timeline to events truth needs. Comment text and account names are dropped."""
    events: list[TimelineEvent] = []
    try:
        for item in items:
            kind = item.get("event")
            if kind not in KEPT_EVENTS:
                continue
            event = TimelineEvent(
                event=kind,
                created_at=_when(item.get("created_at")),
                actor_type=(item.get("actor") or item.get("user") or {}).get("type"),
            )
            if kind == "commented":
                body = item.get("body") or ""
                event.refs, event.outside_refs = references(body, repo)
                event.says_duplicate = bool(_DUPLICATE_WORD.search(body))
            elif kind in ("labeled", "unlabeled"):
                event.label = item["label"]["name"]
            elif kind == "closed":
                event.state_reason = item.get("state_reason")
            elif kind == "renamed":
                event.old_title = (item.get("rename") or {}).get("from")
            elif kind == "cross-referenced":
                source = (item.get("source") or {}).get("issue") or {}
                if source.get("number"):
                    event.refs = [int(source["number"])]
            events.append(event)
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise FeedError(f"A timeline no longer parses: {type(exc).__name__}: {exc}") from exc
    return events


def resolve_token() -> str | None:
    """A GitHub token from the environment or a .env file, if one was provided."""
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None


class GitHubClient:
    """Paced, read-only requests with typed results.

    `max_wait_s` bounds how long a call may sleep for the quota to reset. A scheduled
    run uses a short bound and fails loudly; the backfill waits as long as it takes.
    """

    def __init__(
        self,
        repo: str,
        *,
        token: str | None = None,
        user_agent: str = "issue-triage-desk/0.1",
        transport: httpx.BaseTransport | None = None,
        max_wait_s: float = 3700.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        pace: bool = True,
    ):
        self.repo = repo
        self.has_token = bool(token)
        headers = {
            "User-Agent": user_agent,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(base_url=API, headers=headers, timeout=60, transport=transport)
        self._max_wait_s = max_wait_s
        self._sleep = sleep
        self._clock = clock
        self._pace = pace
        self._last: dict[str, float] = {}
        self.requests = {"search": 0, "core": 0}
        self.remaining: dict[str, int | None] = {"search": None, "core": None}

    def close(self) -> None:
        self._http.close()

    # -- transport -----------------------------------------------------------------

    def _wait_turn(self, resource: str) -> None:
        if not self._pace or resource not in self._last:
            return
        pause = SEARCH_PAUSE[self.has_token] if resource == "search" else CORE_PAUSE
        due = self._last[resource] + pause
        now = self._clock()
        if due > now:
            self._sleep(due - now)

    def _get(self, path: str, params: dict, resource: str) -> httpx.Response:
        last_error = "no response"
        for attempt in range(6):
            self._wait_turn(resource)
            try:
                response = self._http.get(path, params=params)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}"
                self._last[resource] = self._clock()
                self._sleep(min(60.0, 2.0**attempt))
                continue
            self._last[resource] = self._clock()
            self.requests[resource] += 1
            remaining = response.headers.get("x-ratelimit-remaining")
            if remaining is not None and remaining.isdigit():
                self.remaining[resource] = int(remaining)
            if response.status_code == 200:
                return response
            if response.status_code in (403, 429):
                self._wait_for_quota(response, resource)
                continue
            if response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                self._sleep(min(60.0, 2.0**attempt))
                continue
            raise FeedError(f"GitHub answered HTTP {response.status_code} for {path}")
        raise FeedError(f"GitHub did not answer {path} after six attempts ({last_error})")

    def _wait_for_quota(self, response: httpx.Response, resource: str) -> None:
        retry_after = response.headers.get("retry-after")
        reset = response.headers.get("x-ratelimit-reset")
        now = self._clock()
        if retry_after and retry_after.isdigit():
            wait = float(retry_after) + 1.0
        elif reset and reset.isdigit() and response.headers.get("x-ratelimit-remaining") == "0":
            wait = max(1.0, float(reset) - now + 2.0)
        else:
            # A secondary limit without a stated wait: GitHub asks for at least a minute.
            wait = 65.0
        if wait > self._max_wait_s:
            raise RateLimited(
                f"GitHub's {resource} quota is used up; it resets in {wait / 60:.0f} minutes.",
                reset_at=datetime.fromtimestamp(now + wait, UTC),
            )
        self._sleep(wait)

    # -- endpoints -----------------------------------------------------------------

    def search_page(self, query: str, page: int = 1) -> tuple[int, list[Issue]]:
        """One page of an issue search, oldest first: the total count and the issues."""
        for _ in range(3):
            response = self._get(
                "/search/issues",
                {"q": query, "sort": "created", "order": "asc", "per_page": PER_PAGE, "page": page},
                "search",
            )
            try:
                payload = response.json()
                total = int(payload["total_count"])
                items = payload["items"]
                incomplete = bool(payload.get("incomplete_results"))
            except (ValueError, KeyError, TypeError) as exc:
                raise FeedError(f"A search response no longer parses: {exc}") from exc
            if not incomplete:
                return total, [parse_issue(item) for item in items if "pull_request" not in item]
            self._sleep(5.0)
        raise FeedError("GitHub returned incomplete search results three times in a row.")

    def window_query(self, start: datetime, end: datetime, extra: str = "") -> str:
        stamp = "%Y-%m-%dT%H:%M:%SZ"
        created = f"created:{start.astimezone(UTC):{stamp}}..{end.astimezone(UTC):{stamp}}"
        return f"repo:{self.repo} is:issue {created} {extra}".strip()

    def issues_created(self, start: datetime, end: datetime, extra: str = "") -> Iterator[Issue]:
        """Every issue created in [start, end], oldest first.

        A window with more than 1,000 issues is split in half, since search stops there.
        """
        query = self.window_query(start, end, extra)
        total, issues = self.search_page(query, 1)
        if total > SEARCH_LIMIT:
            middle = start + (end - start) / 2
            middle = middle.replace(microsecond=0)
            if middle <= start or middle >= end:
                raise FeedError(f"More than {SEARCH_LIMIT} issues were created within one second.")
            yield from self.issues_created(start, middle, extra)
            yield from self.issues_created(_next_second(middle), end, extra)
            return
        seen = 0
        page = 1
        while True:
            yield from issues
            seen += len(issues)
            if seen >= total or not issues:
                return
            page += 1
            _, issues = self.search_page(query, page)

    def count(self, query: str) -> int:
        total, _ = self.search_page(f"repo:{self.repo} is:issue {query}".strip(), 1)
        return total

    def labels(self) -> list[LabelInfo]:
        """The repository's labels with their descriptions."""
        found: list[LabelInfo] = []
        page = 1
        while True:
            response = self._get(
                f"/repos/{self.repo}/labels", {"per_page": PER_PAGE, "page": page}, "core"
            )
            try:
                batch = [
                    LabelInfo(name=item["name"], description=item.get("description") or "")
                    for item in response.json()
                ]
            except (ValueError, KeyError, TypeError, ValidationError) as exc:
                raise FeedError(f"The label list no longer parses: {exc}") from exc
            found.extend(batch)
            if len(batch) < PER_PAGE:
                return found
            page += 1

    def timeline(self, number: int) -> list[TimelineEvent]:
        """An issue's timeline, reduced. One request for up to 100 entries."""
        items: list[dict] = []
        page = 1
        while True:
            response = self._get(
                f"/repos/{self.repo}/issues/{number}/timeline",
                {"per_page": PER_PAGE, "page": page},
                "core",
            )
            try:
                batch = response.json()
                if not isinstance(batch, list):
                    raise TypeError("expected a list")
            except (ValueError, TypeError) as exc:
                raise FeedError(f"The timeline of issue {number} no longer parses: {exc}") from exc
            items.extend(batch)
            if len(batch) < PER_PAGE or page >= 5:
                return parse_timeline(items, self.repo)
            page += 1

    def rate_limit(self) -> dict[str, tuple[int, int]]:
        """Remaining and total requests per resource. This call costs no quota."""
        response = self._http.get("/rate_limit")
        if response.status_code != 200:
            raise FeedError(f"GitHub answered HTTP {response.status_code} for /rate_limit")
        resources = response.json()["resources"]
        return {
            name: (resources[name]["remaining"], resources[name]["limit"])
            for name in ("core", "search")
        }


def _next_second(moment: datetime) -> datetime:
    from datetime import timedelta

    return moment + timedelta(seconds=1)
