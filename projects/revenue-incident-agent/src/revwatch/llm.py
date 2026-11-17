"""One interface for every model call: `step(request) -> response` over plain data.

The agent loop is hand-written on top of this and does not use the SDK's tool
runner, because each model step has to be keyed, recorded and replayed.
Providers wrap each other: `CassetteProvider(CappedProvider(AnthropicProvider(), 2.0))`.

`CassetteProvider` records each step to disk and replays it, which makes the
evaluation, the tests and CI deterministic and free once a run has been recorded.
Nothing in this file calls the API unless a live provider is built on purpose.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

# The cheapest current Claude model. A larger one needs evaluation evidence.
DEFAULT_MODEL = "claude-haiku-5-5"
# Set explicitly so that cost does not depend on a model's default.
DEFAULT_EFFORT = "low"
# USD per million tokens (input, output). Anthropic list prices as of 2026-11.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
CACHE_READ_FACTOR = 0.1
CACHE_WRITE_FACTOR = 1.25
API_KEY_VARS = ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "claude_api_key")


class LLMError(RuntimeError):
    """The model call failed or returned nothing usable. The incident goes to a person."""


class CassetteMiss(LLMError):
    """No recording for this request and no live provider attached."""


class SpendLimit(LLMError):
    """The ceiling on what live calls may cost in this run is reached."""


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class StepRequest(BaseModel):
    model: str
    effort: str
    system: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = []
    max_tokens: int = 4000
    tag: str = ""  # the incident id, for traces and recordings; not part of the key

    def key(self) -> str:
        """Everything that shapes the answer. Change any of it and the recording is a miss."""
        payload = json.dumps(self.model_dump(exclude={"tag"}), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class StepResponse(BaseModel):
    content: list[dict[str, Any]]
    stop_reason: str
    model: str
    usage: Usage = Usage()
    latency_s: float = 0.0


class LLMProvider(Protocol):
    def step(self, request: StepRequest) -> StepResponse: ...


def priced_as(model: str) -> str:
    """The price-table entry for a model id.

    The API may answer with a dated id, which is priced as the model it names. An
    unknown model is priced at the most expensive known rate, never as free.
    """
    named = [known for known in PRICES if model.startswith(known)]
    if named:
        return max(named, key=len)
    return max(PRICES, key=lambda known: PRICES[known][1])


def cost_usd(model: str, usage: Usage) -> float:
    price_in, price_out = PRICES[priced_as(model)]
    return (
        usage.input_tokens * price_in
        + usage.output_tokens * price_out
        + usage.cache_read_tokens * price_in * CACHE_READ_FACTOR
        + usage.cache_write_tokens * price_in * CACHE_WRITE_FACTOR
    ) / 1_000_000


def resolve_api_key() -> str | None:
    """A key from the environment or a .env file in this or a parent directory.

    `ANTHROPIC_API_KEY` is the standard name; the other spellings are accepted
    as aliases. Real environment variables win over the file. The key is
    returned to the caller and never logged.
    """
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
    return next((os.environ[name] for name in API_KEY_VARS if os.environ.get(name)), None)


class ScriptedProvider:
    """Stands in for a model in tests: a list of responses, or a function of the request."""

    def __init__(
        self, script: list[StepResponse | Exception] | Callable[[StepRequest], StepResponse]
    ) -> None:
        self.script = script
        self.requests: list[StepRequest] = []
        self._lock = threading.Lock()

    def step(self, request: StepRequest) -> StepResponse:
        with self._lock:
            self.requests.append(request)
            item = self.script(request) if callable(self.script) else self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class CassetteProvider:
    """Replay recorded steps; on a miss ask `inner` and, if `record`, save the answer.

    The key covers the model, the effort, the system prompt, the tool definitions
    and the full message history, so a changed prompt or tool is a miss and
    never a stale answer. The tools are deterministic on a seeded world, so a
    recorded investigation replays exactly.
    """

    def __init__(self, path: Path, inner: LLMProvider | None = None, record: bool = False) -> None:
        self.path, self.inner, self.record = Path(path), inner, record
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = (
            json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        )

    def __len__(self) -> int:
        return len(self._entries)

    def step(self, request: StepRequest) -> StepResponse:
        key = request.key()
        with self._lock:
            entry = self._entries.get(key)
        # A recorded failure replays as the same failure, so a recorded run replays
        # completely. With a live provider attached it is tried again instead.
        if entry is not None and not ("error" in entry and self.inner is not None):
            if "error" in entry:
                raise LLMError(entry["error"])
            return StepResponse.model_validate(entry["response"])
        if self.inner is None:
            raise CassetteMiss(
                f"no recorded response in {self.path.name} for {request.tag or 'this request'} "
                "and live calls are off. Run with --live (needs a key); add --record to "
                "save the responses."
            )
        try:
            response = self.inner.step(request)
        except (CassetteMiss, SpendLimit):
            raise  # nothing was asked, so there is no answer to record
        except LLMError as exc:
            if self.record:
                self._save(key, request, {"error": str(exc)})
            raise
        if self.record:
            self._save(key, request, {"response": response.model_dump()})
        return response

    def _save(self, key: str, request: StepRequest, outcome: dict[str, Any]) -> None:
        with self._lock:
            entries = dict(self._entries)
            entries[key] = {"tag": request.tag, "model": request.model, **outcome}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(entries, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
            os.replace(tmp, self.path)  # atomic: an interrupted run keeps what it paid for
            self._entries = entries


class CappedProvider:
    """Stops live calls once what they have cost reaches `limit_usd`.

    It wraps the live provider, inside the recording one, so a replayed step
    never counts. The check runs before a call is sent and the cost is known
    only when the answer is back: calls already in flight finish, so a run can
    pass the limit by what one call per concurrent investigation costs. A call
    that fails is not counted.
    """

    def __init__(self, inner: LLMProvider, limit_usd: float) -> None:
        self.inner, self.limit_usd = inner, limit_usd
        self.spent_usd = 0.0
        self._lock = threading.Lock()

    def step(self, request: StepRequest) -> StepResponse:
        with self._lock:
            if self.spent_usd >= self.limit_usd:
                raise SpendLimit(
                    f"live calls have cost US${self.spent_usd:.2f}, which reaches the limit of "
                    f"US${self.limit_usd:.2f}; nothing more was sent. Recorded responses are "
                    "kept. Raise --max-usd to go on."
                )
        response = self.inner.step(request)
        with self._lock:
            self.spent_usd += cost_usd(response.model, response.usage)
        return response


class AnthropicProvider:
    """Claude through the official SDK. At most `concurrency` requests are in flight."""

    def __init__(self, client: Any = None, concurrency: int = 8) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(max(1, concurrency))

    @property
    def client(self) -> Any:
        with self._lock:
            if self._client is None:
                import anthropic

                key = resolve_api_key()
                # Without a key the SDK falls back to its own credential resolution.
                self._client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
            return self._client

    def step(self, request: StepRequest) -> StepResponse:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            # One cache breakpoint on the system prompt covers the tool definitions too:
            # they are the same for every investigation.
            "system": [
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": request.messages,
            "output_config": {"effort": request.effort},
        }
        if request.tools:
            kwargs["tools"] = request.tools
        client = self.client
        try:
            with self._slots:
                started = time.perf_counter()
                response = client.messages.create(**kwargs)
                latency = time.perf_counter() - started
        except Exception as exc:  # SDK errors: credentials, rate limit, status, connection
            raise LLMError(f"Anthropic call failed: {type(exc).__name__}: {exc}") from exc
        usage = response.usage
        return StepResponse(
            # Blocks go back to the API unchanged on the next step, thinking included.
            content=[
                block.model_dump(mode="json", exclude_none=True, by_alias=True)
                for block in response.content
            ],
            stop_reason=response.stop_reason or "",
            model=getattr(response, "model", None) or request.model,
            usage=Usage(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            ),
            latency_s=round(latency, 3),
        )
