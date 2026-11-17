"""The live stream: Wikimedia's recent-changes events over server-sent events.

The stream carries every change to every wiki, about forty events a second. The desk
keeps edits to articles of one wiki that were not made by a bot. When the connection
drops, it reconnects and sends the id of the last event it saw, so the server resumes
from there and nothing is lost or read twice.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from .config import Settings
from .models import Edit
from .scrub import is_temp_account, scrub_comment
from .wiki import FeedError

STREAM_URL = "https://stream.wikimedia.org/v2/stream/recentchange"
# After this many failed reconnects in a row the reader gives up.
MAX_RECONNECTS = 5


def parse_sse(lines: Iterable[str]) -> Iterator[tuple[str | None, str]]:
    """Server-sent events as (event id, data). An event ends at a blank line."""
    event_id: str | None = None
    data: list[str] = []
    for line in lines:
        if line == "":
            if data:
                yield event_id, "\n".join(data)
            data = []
            continue
        if line.startswith(":"):
            continue  # a comment, sent as a keep-alive
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "id":
            event_id = value
        elif field == "data":
            data.append(value)
    if data:
        yield event_id, "\n".join(data)


def parse_event(payload: dict, settings: Settings) -> Edit | None:
    """An event as an edit, or None if it is not an article edit by a person on this wiki.

    The account name is read once, to tell a temporary account from a registered one.
    It is not kept.
    """
    if payload.get("type") != "edit" or payload.get("wiki") != settings.wiki_db:
        return None
    if payload.get("namespace") != settings.namespace or payload.get("bot"):
        return None
    try:
        meta = payload.get("meta") or {}
        event_time = meta.get("dt")
        return Edit(
            rev_id=payload["revision"]["new"],
            parent_id=payload["revision"].get("old") or 0,
            title=payload["title"],
            timestamp=datetime.fromtimestamp(payload["timestamp"], UTC),
            minor=bool(payload.get("minor")),
            bot=False,
            temp=is_temp_account(payload.get("user")),
            old_len=(payload.get("length") or {}).get("old") or 0,
            new_len=(payload.get("length") or {}).get("new") or 0,
            comment=scrub_comment(payload.get("comment") or ""),
            event_time=event_time.replace("Z", "+00:00") if event_time else None,
        )
    except (KeyError, TypeError, ValueError, AttributeError, ValidationError) as exc:
        raise FeedError(f"A stream event no longer parses: {exc}") from exc


def open_stream(user_agent: str, last_event_id: str | None) -> Iterator[str]:
    """Lines of the live stream, resumed after `last_event_id` if one is given."""
    headers = {"User-Agent": user_agent, "Accept": "text/event-stream"}
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id
    with httpx.Client(timeout=httpx.Timeout(30.0, read=60.0)) as http:
        with http.stream("GET", STREAM_URL, headers=headers) as response:
            if response.status_code != 200:
                raise FeedError(f"The stream answered HTTP {response.status_code}")
            yield from response.iter_lines()


class StreamReader:
    """Yields edits from the stream until told to stop, reconnecting after a drop."""

    def __init__(
        self,
        settings: Settings,
        opener: Callable[[str | None], Iterator[str]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings
        self._open = opener or (lambda last: open_stream(settings.user_agent, last))
        self._sleep = sleep
        self.last_event_id: str | None = None
        self.events = 0
        self.kept = 0
        self.reconnects = 0

    def edits(self, stop: Callable[[], bool]) -> Iterator[Edit]:
        failures = 0
        while not stop():
            try:
                for event_id, data in parse_sse(self._open(self.last_event_id)):
                    if event_id:
                        self.last_event_id = event_id
                    self.events += 1
                    failures = 0
                    try:
                        payload = json.loads(data)
                    except ValueError as exc:
                        raise FeedError("A stream event is not JSON.") from exc
                    edit = parse_event(payload, self.settings)
                    if edit is not None:
                        self.kept += 1
                        yield edit
                    if stop():
                        return
            except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
                failures += 1
                if failures > MAX_RECONNECTS:
                    raise FeedError(
                        f"The stream dropped {failures} times in a row ({type(exc).__name__})."
                    ) from exc
                self._sleep(min(30.0, 2.0**failures))
            # The server closes the connection now and then; that is not an error.
            self.reconnects += 1
