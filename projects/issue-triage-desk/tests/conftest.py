"""Shared test helpers: issues written for the tests and a fake GitHub."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from triagedesk.config import GroupConfig, Settings
from triagedesk.feed import GitHubClient
from triagedesk.models import Issue
from triagedesk.store import Store

REPO = "example/engine"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def make_issue(number: int, title: str, body: str = "", *, day: float = 0, labels=(), state="open",
               state_reason=None, closed_day: float | None = None) -> Issue:  # fmt: skip
    return Issue(
        number=number,
        title=title,
        body=body,
        created_at=T0 + timedelta(days=day),
        updated_at=T0 + timedelta(days=day),
        closed_at=(T0 + timedelta(days=closed_day)) if closed_day is not None else None,
        state=state,
        state_reason=state_reason,
        labels=list(labels),
    )


def api_item(issue: Issue, **extra) -> dict:
    """An issue in the shape GitHub's API returns, with fields the desk must ignore."""
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    item = {
        "number": issue.number,
        "title": issue.title,
        "body": issue.body or None,
        "created_at": issue.created_at.strftime(stamp),
        "updated_at": (issue.updated_at or issue.created_at).strftime(stamp),
        "closed_at": issue.closed_at.strftime(stamp) if issue.closed_at else None,
        "state": issue.state,
        "state_reason": issue.state_reason,
        "labels": [{"name": name, "color": "ededed"} for name in issue.labels],
        "user": {"login": "someone", "type": "User"},
        "assignees": [{"login": "someone-else"}],
    }
    item.update(extra)
    return item


class FakeGitHub:
    """Answers search, labels and timeline requests from lists held in memory."""

    def __init__(self, issues=(), labels=(), timelines=None):
        self.issues = list(issues)
        self.labels = list(labels)
        self.timelines = dict(timelines or {})
        self.calls: list[tuple[str, dict]] = []
        self.fail_next: list[httpx.Response] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.calls.append((request.url.path, params))
        if self.fail_next:
            return self.fail_next.pop(0)
        if request.url.path == "/search/issues":
            return self._search(params)
        if request.url.path.endswith("/labels"):
            return httpx.Response(200, json=self.labels)
        if request.url.path.endswith("/timeline"):
            number = int(request.url.path.split("/")[-2])
            return httpx.Response(200, json=self.timelines.get(number, []))
        if request.url.path == "/rate_limit":
            body = {"resources": {"core": {"remaining": 55, "limit": 60},
                                  "search": {"remaining": 9, "limit": 10}}}  # fmt: skip
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={"message": "Not Found"})

    def _search(self, params: dict) -> httpx.Response:
        query = params["q"]
        window = next(part for part in query.split() if part.startswith("created:"))
        start, end = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            for value in window.removeprefix("created:").split("..")
        )
        found = sorted(
            (i for i in self.issues if start <= i.created_at <= end), key=lambda i: i.created_at
        )
        if "reason:duplicate" in query:
            found = [i for i in found if i.closed_as_duplicate]
        page = int(params.get("page", 1))
        per_page = int(params.get("per_page", 100))
        chunk = found[(page - 1) * per_page : page * per_page]
        body = {
            "total_count": len(found),
            "incomplete_results": False,
            "items": [api_item(issue) for issue in chunk],
        }
        return httpx.Response(200, json=body, headers={"x-ratelimit-remaining": "7"})


@pytest.fixture
def fake():
    return FakeGitHub()


def make_client(fake: FakeGitHub, **kwargs) -> GitHubClient:
    kwargs.setdefault("pace", False)
    kwargs.setdefault("sleep", lambda seconds: None)
    return GitHubClient(REPO, transport=httpx.MockTransport(fake.handler), **kwargs)


@pytest.fixture
def client(fake):
    return make_client(fake)


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def settings(tmp_path):
    return Settings(
        repo=REPO,
        groups=(GroupConfig("area", "topic:", 2), GroupConfig("platform", "platform:", 2)),
        home=tmp_path,
    )


def dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True)


# -- a small world: training issues with separable areas, then a few new ones ------

AREAS = {
    "topic:gui": "button label theme font container panel menu popup scrollbar focus",
    "topic:physics": "collision rigidbody gravity raycast joint velocity impulse contact shape solver",
    "topic:audio": "sound volume mixer playback stream bus reverb pitch microphone clip",
}


def world_issues(per_area: int = 30) -> list[Issue]:
    """Training issues, three areas, opened one a day from T0. Invented text."""
    issues = []
    number = 1
    for round_ in range(per_area):
        for label, vocabulary in AREAS.items():
            terms = vocabulary.split()
            a, b, c = (terms[(round_ + k) % len(terms)] for k in range(3))
            labels = [label, "bug"]
            body = (
                f"### Issue description\nThe {a} breaks when the {b} changes and the {c} is reset."
            )
            if round_ % 6 == 0:
                labels.append("platform:web")
                body += " Only in the browser export, the web build shows it."
            issues.append(
                make_issue(
                    number,
                    f"{a.capitalize()} fails with {b} ({number})",
                    body,
                    day=number * 0.9,
                    labels=labels,
                )
            )
            number += 1
    return issues


class ScriptedProvider:
    """A stand-in model: answers come from a function of the two prompts."""

    model = "scripted-model"
    params = {"effort": "low"}

    def __init__(self, answer=None, fail: bool = False, replayed: bool = False):
        from triagedesk.models import Triage

        self.calls: list[tuple[str, str]] = []
        self.fail = fail
        self.replayed = replayed
        self.answer = answer or (
            lambda system, user: Triage(
                labels=[],
                duplicate_of=None,
                duplicate_confidence="high",
                note="No answer scripted.",
            )
        )

    def structured(self, *, system, user, schema):
        from triagedesk.llm import CallStats, LLMError

        self.calls.append((system, user))
        if self.fail:
            raise LLMError("scripted failure")
        stats = CallStats("claude-haiku-5-5", 3000, 60, 0.8, replayed=self.replayed)
        return self.answer(system, user), stats


def make_desk(tmp_path, provider=None, budget=None, issues=None, **changes):
    """A desk trained on the small world, cut on day 100."""
    from dataclasses import replace
    from datetime import date

    from triagedesk.backtest import build_desk
    from triagedesk.embed import HashEmbedder

    settings = Settings(
        repo=REPO,
        groups=(GroupConfig("area", "topic:", 5), GroupConfig("platform", "platform:", 3)),
        history_start=date(2025, 12, 1),
        cut=date(2026, 4, 11),
        held_out_start=date(2026, 5, 1),
        test_end=date(2026, 5, 31),
        duplicate_confident=0.95,
        duplicate_propose=0.80,
        home=tmp_path,
    )
    if changes:
        settings = replace(settings, **changes)
    store = Store(":memory:")
    store.add_issues(issues if issues is not None else world_issues())
    return build_desk(
        settings, store, HashEmbedder(), cut=settings.cut, provider=provider, budget=budget
    )
