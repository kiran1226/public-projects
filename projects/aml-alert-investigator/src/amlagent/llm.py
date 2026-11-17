"""One interface for every model call: `step(request) -> response` over plain data.

The agent loop is hand-written on top of this instead of using the SDK tool runner,
because each model step has to be keyed, recorded, replayed, budget-checked and
leak-scanned. Providers wrap each other:

    GuardedProvider( CassetteProvider( BudgetedProvider( RouterProvider(...) ) ) )
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from .privacy import OutboundScanner, outbound_text

FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-sonnet-5-5"})
# USD per million tokens (input, output). Anthropic list prices as of 2026-11.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    # Models a refusal fallback can be served by.
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
}
# Cache reads, USD per million tokens, where they differ from a tenth of the input price.
CACHE_READ = {"claude-opus-5-5": 0.20, "claude-sonnet-5-5": 0.10}
CACHE_WRITE_FACTOR = 1.25


class LLMError(RuntimeError):
    """The model call failed or returned nothing usable. Callers route to a person."""


class CassetteMiss(LLMError):
    """No recording for this request and no live provider attached."""


class BudgetExceeded(LLMError):
    """The spend cap would be passed. The run stops; finished cases are kept."""


class LeakBlocked(LLMError):
    def __init__(self, kinds: list[str]):
        super().__init__(f"Outbound check blocked the request: {', '.join(kinds)}")
        self.kinds = kinds


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class StepRequest(BaseModel):
    model: str
    effort: str = "low"
    system: str
    messages: list[dict]
    tools: list[dict] = []
    output_schema: dict | None = None
    max_tokens: int = 4000
    tag: str = ""  # "<alert>/<role>", for traces and recordings; not part of the key

    def key(self) -> str:
        payload = json.dumps(self.model_dump(exclude={"tag"}), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class StepResponse(BaseModel):
    content: list[dict]
    stop_reason: str
    model: str
    usage: Usage = Usage()
    latency_s: float = 0.0


class LLMProvider(Protocol):
    def step(self, request: StepRequest) -> StepResponse: ...


def is_metered(model: str) -> bool:
    """NVIDIA-hosted trial endpoints (ids like 'vendor/model') are not priced per token."""
    return "/" not in model


def priced_as(model: str) -> str:
    """The price-table entry for a model id.

    The API may answer with a dated id, which is priced as the model it names. An
    unknown Claude model is priced at the most expensive known rate, never as free.
    """
    named = [known for known in PRICES if model.startswith(known)]
    if named:
        return max(named, key=len)
    return max(PRICES, key=lambda known: PRICES[known][1])


def cost_usd(model: str, usage: Usage) -> float:
    if not is_metered(model):
        return 0.0
    known = priced_as(model)
    price_in, price_out = PRICES[known]
    return (
        usage.input_tokens * price_in
        + usage.output_tokens * price_out
        + usage.cache_read_tokens * CACHE_READ.get(known, price_in * 0.1)
        + usage.cache_write_tokens * price_in * CACHE_WRITE_FACTOR
    ) / 1_000_000


def resolve_key(*names: str) -> str | None:
    """Find a key in the environment or a .env file in this or a parent directory."""
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
    return next((os.environ[n] for n in names if os.environ.get(n)), None)


class ScriptedProvider:
    """Stands in for a model in tests: a list of responses, or a function of the request."""

    def __init__(self, script: list | Callable[[StepRequest], StepResponse]):
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

    The key covers everything that shapes the answer, so a changed prompt is a miss and
    never a stale answer. Each entry also stores what this step added to the outbound
    request (the last message, plus the system prompt on a first step), so the leak scan
    can read everything that was ever sent, once.
    """

    def __init__(self, path: Path, inner: LLMProvider | None = None, record: bool = False):
        self.path, self.inner, self.record = Path(path), inner, record
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = (
            json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        )

    def step(self, request: StepRequest) -> StepResponse:
        key = request.key()
        with self._lock:
            entry = self._entries.get(key)
        # A recorded failure replays as the same failure, so a recorded run replays
        # completely. With a live provider attached it is retried instead.
        if entry is not None and not ("error" in entry and self.inner is not None):
            if "error" in entry:
                raise LLMError(entry["error"])
            return StepResponse.model_validate(entry["response"])
        if self.inner is None:
            raise CassetteMiss(
                f"No recorded response in {self.path.name} for {request.tag or 'this request'}. "
                "Run with --live (needs a key); add --record to save responses."
            )
        try:
            response = self.inner.step(request)
        except (CassetteMiss, BudgetExceeded, LeakBlocked):
            raise  # these are about the run, not about what the model answered
        except LLMError as exc:
            if self.record:
                self._save(key, request, {"error": str(exc)})
            raise
        if self.record:
            self._save(key, request, {"response": response.model_dump()})
        return response

    def _save(self, key: str, request: StepRequest, outcome: dict) -> None:
        with self._lock:
            entries = dict(self._entries)
            entries[key] = {
                "tag": request.tag,
                "model": request.model,
                "system": request.system if len(request.messages) == 1 else None,
                "sent": request.messages[-1],
                **outcome,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(entries, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
            os.replace(tmp, self.path)  # atomic: an interrupted run keeps what it paid for
            self._entries = entries

    def sent(self) -> list[tuple[str, str]]:
        """(tag, text our side sent at that step) for every recorded step."""
        return [
            (e["tag"], outbound_text(e["system"] or "", [e["sent"]]))
            for e in self._entries.values()
        ]


class GuardedProvider:
    """The outbound check: scans what our side sends just before it leaves."""

    def __init__(self, inner: LLMProvider, scanner: OutboundScanner):
        self.inner, self.scanner = inner, scanner

    def step(self, request: StepRequest) -> StepResponse:
        kinds = self.scanner.scan(outbound_text(request.system, request.messages))
        if kinds:
            raise LeakBlocked(kinds)
        return self.inner.step(request)


class RouterProvider:
    """Sends each request to the vendor that serves its model."""

    def __init__(self, anthropic: LLMProvider | None = None, nvidia: LLMProvider | None = None):
        self.anthropic, self.nvidia = anthropic, nvidia

    def step(self, request: StepRequest) -> StepResponse:
        provider = self.anthropic if is_metered(request.model) else self.nvidia
        if provider is None:
            raise LLMError(f"No live provider is configured for {request.model}.")
        return provider.step(request)


class AnthropicProvider:
    """Claude through the official SDK."""

    def __init__(self, client=None):
        self._client = client
        self._lock = threading.Lock()

    @property
    def client(self):
        with self._lock:
            if self._client is None:
                import anthropic

                key = resolve_key("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "claude_api_key")
                self._client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
            return self._client

    def step(self, request: StepRequest) -> StepResponse:
        kwargs: dict = dict(
            model=request.model,
            max_tokens=request.max_tokens,
            # One cache breakpoint on the system prompt covers the tool definitions too.
            system=[
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ],
            messages=request.messages,
            # Effort is explicit so cost does not depend on a model default.
            output_config={"effort": request.effort},
        )
        if request.tools:
            kwargs["tools"] = request.tools
        if request.output_schema:
            kwargs["output_config"]["format"] = {
                "type": "json_schema",
                "schema": request.output_schema,
            }
        client = self.client
        started = time.perf_counter()
        try:
            if request.model in FALLBACK_MODELS:
                # If a safety classifier declines, the API retries on a fallback model.
                response = client.beta.messages.create(
                    **kwargs, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = client.messages.create(**kwargs)
        except Exception as exc:  # SDK errors (auth, rate limit, status, connection) and others
            raise LLMError(f"Anthropic call failed: {type(exc).__name__}: {exc}") from exc
        usage = response.usage
        return StepResponse(
            # Blocks go back to the API unchanged on the next step, thinking included.
            # by_alias keeps the API's own field names (a fallback block's `from`).
            content=[
                b.model_dump(mode="json", exclude_none=True, by_alias=True)
                for b in response.content
            ],
            stop_reason=response.stop_reason or "",
            model=getattr(response, "model", None) or request.model,
            usage=Usage(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            ),
            latency_s=round(time.perf_counter() - started, 3),
        )
