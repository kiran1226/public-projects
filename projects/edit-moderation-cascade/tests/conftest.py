"""Shared test helpers: edits written for the tests, a fake wiki and a scripted model.

Every title, summary, line of page text and account name here is invented. Nothing in
the tests opens a connection: the wiki is `FakeWiki` behind `httpx.MockTransport`, and
clocks and sleeps are injected.
"""

from __future__ import annotations

import json
import random
import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from editwatch.config import Settings
from editwatch.llm import DEFAULT_MODEL, CallStats, LLMError
from editwatch.models import Edit, Prediction, Tier2Answer, Verdict
from editwatch.store import Store
from editwatch.tier1 import SHARES, Tier1, train
from editwatch.wiki import WikiClient

T0 = datetime(2026, 11, 1, tzinfo=UTC)
USER_AGENT = "editwatch-tests/0 (offline; no request leaves this process)"
# Invented account names. None of them may survive in anything the desk keeps.
ACCOUNT = "ExampleEditor42"
ACCOUNTS = ("ExampleEditor42", "SampleAccount7", "TestWriter2026", "Invented_User_99")
STAMP = "%Y-%m-%dT%H:%M:%SZ"


def at(minutes: float = 0, **more) -> datetime:
    """A moment `minutes` after T0."""
    return T0 + timedelta(minutes=minutes, **more)


def make_edit(rev_id: int, *, minute: float = 0, **fields) -> Edit:
    """An edit to an invented article, made `minute` minutes after T0."""
    values = {
        "rev_id": rev_id,
        "parent_id": rev_id - 1,
        "title": "Example article",
        "timestamp": at(minute),
        "old_len": 1000,
        "new_len": 1040,
        "comment": "/* History */ added a reference",
    }
    values.update(fields)
    return Edit(**values)


def api_item(edit: Edit, tags=(), **extra) -> dict:
    """An edit as the recent-changes list returns it, with fields the desk must ignore."""
    item = {
        "type": "edit",
        "ns": 0,
        "title": edit.title,
        "pageid": 500 + edit.rev_id % 7,
        "revid": edit.rev_id,
        "old_revid": edit.parent_id,
        "rcid": 9_000_000 + edit.rev_id,
        "user": ACCOUNT,
        "userid": 4242,
        "timestamp": edit.timestamp.strftime(STAMP),
        "minor": edit.minor,
        "bot": edit.bot,
        "new": False,
        "oldlen": edit.old_len,
        "newlen": edit.new_len,
        "comment": edit.comment,
        "tags": list(tags),
    }
    if edit.temp:
        item["temp"] = True
    item.update(extra)
    return item


def stream_event(edit: Edit, *, user: str = ACCOUNT, **extra) -> dict:
    """An edit as the live stream reports it, account name included."""
    event_time = edit.event_time or edit.timestamp + timedelta(seconds=2)
    payload = {
        "$schema": "/mediawiki/recentchange/1.0.0",
        "meta": {
            "domain": "en.wikipedia.org",
            "stream": "mediawiki.recentchange",
            "dt": event_time.strftime(STAMP),
        },
        "id": 7_000_000 + edit.rev_id,
        "type": "edit",
        "namespace": 0,
        "title": edit.title,
        "comment": edit.comment,
        "timestamp": int(edit.timestamp.timestamp()),
        "user": user,
        "bot": edit.bot,
        "minor": edit.minor,
        "length": {"old": edit.old_len, "new": edit.new_len},
        "revision": {"old": edit.parent_id, "new": edit.rev_id},
        "wiki": "enwiki",
        "server_name": "en.wikipedia.org",
    }
    payload.update(extra)
    return payload


def sse_lines(events) -> list[str]:
    """(event id, payload) pairs as the lines of a server-sent-events body."""
    lines = []
    for event_id, payload in events:
        lines += ["event: message", f"id: {event_id}", f"data: {json.dumps(payload)}", ""]
    return lines


class FakeClock:
    """A clock that moves only when something sleeps on it or a test advances it."""

    def __init__(self, now: float = 0.0):
        self.now = now
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeWiki:
    """Answers the wiki API and the revert-risk service from lists held in memory.

    `changes` are recent-changes items (see `api_item`), `texts` and `tags` are keyed
    by revision id, `risks` holds the probability the revert-risk model gives. A
    revision in neither `texts` nor `tags` does not exist for this wiki.
    """

    def __init__(self, changes=(), texts=None, tags=None, risks=None):
        self.changes = list(changes)
        self.texts: dict[int, str] = dict(texts or {})
        self.tags: dict[int, list[str]] = dict(tags or {})
        self.risks: dict[int, float] = dict(risks or {})
        self.hidden: set[int] = set()
        # One entry per request: what was asked for, and its parameters or JSON body.
        self.calls: list[tuple[str, dict]] = []
        self.requests: list[httpx.Request] = []
        self.fail_next: list[httpx.Response] = []
        # Called with every request before it is answered, for example to move a clock.
        self.on_request = None

    def asked(self, kind: str) -> list[dict]:
        return [params for name, params in self.calls if name == kind]

    def revids_asked(self) -> list[list[int]]:
        """The revision ids of each revisions request, in the order the requests came."""
        return [[int(r) for r in params["revids"].split("|")] for params in self.asked("revisions")]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.on_request is not None:
            self.on_request(request)
        if request.url.path == "/w/api.php":
            params = dict(request.url.params)
            kind = params.get("list") or params.get("prop") or "other"
        elif request.url.path.endswith(":predict"):
            params = json.loads(request.content)
            kind = "risk"
        else:
            return httpx.Response(404, json={"message": "Not Found"})
        self.calls.append((kind, params))
        if self.fail_next:
            return self.fail_next.pop(0)
        if kind == "recentchanges":
            return self._recent_changes(params)
        if kind == "revisions":
            return self._revisions(params)
        if kind == "risk":
            return self._risk(params)
        return httpx.Response(200, json={"error": {"code": "badvalue", "info": "Unknown request"}})

    def _recent_changes(self, params: dict) -> httpx.Response:
        start = datetime.fromisoformat(params["rcstart"].replace("Z", "+00:00"))
        found = sorted(
            (
                item
                for item in self.changes
                if datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")) >= start
                and str(item.get("ns", 0)) == params["rcnamespace"]
            ),
            key=lambda item: (item["timestamp"], item["revid"]),
        )
        chunk = found[: int(params["rclimit"])]
        return httpx.Response(200, json={"batchcomplete": True, "query": {"recentchanges": chunk}})

    def _revisions(self, params: dict) -> httpx.Response:
        pages: dict[int, dict] = {}
        missing = {}
        for rev_id in (int(r) for r in params["revids"].split("|")):
            if rev_id not in self.texts and rev_id not in self.tags:
                missing[str(rev_id)] = {"revid": rev_id, "missing": True}
                continue
            revision = {"revid": rev_id, "parentid": rev_id - 1}
            if "content" in params["rvprop"]:
                if rev_id in self.hidden or rev_id not in self.texts:
                    revision["slots"] = {"main": {"texthidden": True}}
                else:
                    revision["slots"] = {
                        "main": {
                            "contentmodel": "wikitext",
                            "contentformat": "text/x-wiki",
                            "content": self.texts[rev_id],
                        }
                    }
            if "tags" in params["rvprop"]:
                revision["tags"] = list(self.tags.get(rev_id, []))
            # Several revisions share a page, as they do in a real answer.
            page_id = 500 + rev_id % 3
            page = pages.setdefault(
                page_id, {"pageid": page_id, "ns": 0, "title": "Example article", "revisions": []}
            )
            page["revisions"].append(revision)
        query: dict = {"pages": list(pages.values())}
        if missing:
            query["badrevids"] = missing
        return httpx.Response(200, json={"batchcomplete": True, "query": query})

    def _risk(self, body: dict) -> httpx.Response:
        rev_id = body["rev_id"]
        if rev_id not in self.risks:
            return httpx.Response(400, json={"error": "This revision cannot be scored."})
        risk = self.risks[rev_id]
        return httpx.Response(
            200,
            json={
                "model_name": "revertrisk-language-agnostic",
                "wiki_db": "enwiki",
                "revision_id": rev_id,
                "output": {
                    "prediction": risk >= 0.5,
                    "probabilities": {"true": risk, "false": 1 - risk},
                },
            },
        )


def make_client(fake: FakeWiki, *, clock: FakeClock | None = None, **kwargs) -> WikiClient:
    """A client on the fake wiki. It sleeps on a fake clock, so no test waits."""
    clock = clock or FakeClock()
    kwargs.setdefault("user_agent", USER_AGENT)
    kwargs.setdefault("sleep", clock.sleep)
    kwargs.setdefault("clock", clock)
    return WikiClient(transport=httpx.MockTransport(fake.handler), **kwargs)


# What one scripted model call costs: 3,000 tokens in, 60 out, at the default model's price.
CALL_COST = CallStats(DEFAULT_MODEL, 3000, 60, 0.8).cost_usd


def answer(risk: int = 50, category: str = "other", first_line: int = 0, last_line: int = 0):
    return Tier2Answer(risk=risk, category=category, first_line=first_line, last_line=last_line)


class ScriptedProvider:
    """A stand-in model: answers come from a function of the two prompts."""

    model = "scripted-model"
    params = {"effort": "low"}

    def __init__(self, answer_for=None, fail: bool = False, replayed: bool = False):
        self.calls: list[tuple[str, str]] = []
        self.threads: set[int] = set()
        self.fail = fail
        self.replayed = replayed
        self.answer_for = answer_for or (lambda system, user: answer())

    def structured(self, *, system, user, schema):
        self.calls.append((system, user))
        self.threads.add(threading.get_ident())
        if self.fail:
            raise LLMError("scripted failure")
        stats = CallStats(DEFAULT_MODEL, 3000, 60, 0.8, replayed=self.replayed)
        return self.answer_for(system, user), stats


def training_edits(n: int = 300, seed: int = 7) -> tuple[list[Edit], dict[int, bool]]:
    """Edits with outcomes that a classifier can learn, oldest first.

    One edit in four is a large removal by a temporary account with no summary, and
    nine in ten of those were reverted. The rest are small additions by registered
    accounts with a summary, and few of those were.
    """
    rng = random.Random(seed)
    edits, reverted = [], {}
    for i in range(n):
        rev_id = 10_000_000 + i * 10
        old = rng.randint(4000, 9000)
        if i % 4 == 0:
            edit = make_edit(
                rev_id,
                minute=i * 3,
                parent_id=rev_id - rng.randint(1, 20),
                title=f"Example article {i % 40}",
                temp=True,
                old_len=old,
                new_len=old - rng.randint(2000, 3500),
                comment="",
            )
            reverted[rev_id] = rng.random() < 0.9
        else:
            edit = make_edit(
                rev_id,
                minute=i * 3,
                parent_id=rev_id - rng.randint(2000, 90_000),
                title=f"Example article {i % 40}",
                minor=i % 3 == 0,
                old_len=old,
                new_len=old + rng.randint(5, 400),
                comment=f"/* Section {i % 9} */ "
                + rng.choice(["added a reference", "copy edit", "updated a figure"]),
            )
            reverted[rev_id] = rng.random() < 0.03
        edits.append(edit)
    return edits, reverted


def make_tier1(quantiles: dict[float, float] | None = None, trained_on: int = 1000) -> Tier1:
    """A tier 1 with fixed slice thresholds and no classifier, for tests that pass scores in."""
    if quantiles is None:
        quantiles = dict(zip(SHARES, (0.9, 0.8, 0.7, 0.5, 0.4, 0.3, 0.2), strict=True))
    return Tier1(model=None, trained_on=trained_on, reverted_share=0.2, quantiles=quantiles)


def make_prediction(rev_id: int, risk: float, *, config: str = "cascade", version: str = "v1",
                    tier1: float | None = None, model_risk: int | None = None,
                    category: str | None = None, lines: tuple[int, int] | None = None,
                    skipped: str | None = None, gate=(), cost_usd: float = 0.0,
                    latency_s: float | None = None, minute: float = 0) -> Prediction:  # fmt: skip
    """A log row. With `model_risk` it is a row the model decided, otherwise a tier-1 row."""
    read = model_risk is not None
    return Prediction(
        rev_id=rev_id,
        made_at=at(minute),
        written_at=at(minute, seconds=5),
        system_version=version,
        config=config,
        verdict=Verdict(
            risk=risk,
            tier1=risk if tier1 is None else tier1,
            model_risk=model_risk,
            category=(category or "other") if read else None,
            first_line=lines[0] if lines else None,
            last_line=lines[1] if lines else None,
            source="model" if read else "tier1",
            skipped=skipped,
            gate=list(gate),
        ),
        cost_usd=cost_usd,
        latency_s=latency_s,
    )


@pytest.fixture
def fake():
    return FakeWiki()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def client(fake, clock):
    return make_client(fake, clock=clock)


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def settings(tmp_path):
    return Settings(home=tmp_path)


@pytest.fixture(scope="session")
def trained():
    """Tier 1 trained once on the synthetic set, with the set it was trained on."""
    edits, reverted = training_edits(300)
    return train(edits, reverted), edits, reverted
