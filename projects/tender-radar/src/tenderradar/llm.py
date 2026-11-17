"""Thin model interface: structured output in, typed object out, with record and replay.

Everything above this file depends on `LLMProvider`, not on a vendor SDK.
`CassetteProvider` records responses to disk and replays them, so the evaluation, the
tests and CI run without a key once a run has been recorded.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# The cheapest current model. The evaluation decides whether a larger one is needed.
DEFAULT_MODEL = "claude-haiku-5-5"
# Set explicitly so cost does not depend on a model's default.
DEFAULT_EFFORT = "low"
# USD per million tokens (input, output), prompts up to 100K tokens. List prices, 2026-11.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
API_KEY_VARS = ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "claude_api_key")


def resolve_api_key() -> str | None:
    """Find an API key in the environment or in a .env file in this or a parent directory."""
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
    for name in API_KEY_VARS:
        if os.environ.get(name):
            return os.environ[name]
    return None


class LLMError(RuntimeError):
    """The model call failed or returned nothing usable. The caller goes on without it."""


class CassetteMiss(LLMError):
    """No recorded response for this request and no live provider to ask."""


@dataclass(frozen=True)
class CallStats:
    model: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    # True when the answer came from a recording: it cost nothing today.
    replayed: bool = False

    @property
    def cost_usd(self) -> float:
        price_in, price_out = PRICES.get(self.model, (0.0, 0.0))
        return (self.input_tokens * price_in + self.output_tokens * price_out) / 1_000_000


class LLMProvider(Protocol):
    model: str

    def structured(self, *, system: str, user: str, schema: type[T]) -> tuple[T, CallStats]: ...


class AnthropicProvider:
    """Claude through the official SDK, using structured outputs."""

    def __init__(
        self,
        model: str | None = None,
        client=None,
        max_tokens: int = 4000,
        effort: str | None = None,
        concurrency: int = 8,
    ):
        # At most `concurrency` requests are in flight, however many threads call in.
        self._slots = threading.BoundedSemaphore(max(1, concurrency))
        self._client_lock = threading.Lock()
        self.model = model or os.environ.get("TENDERRADAR_MODEL", DEFAULT_MODEL)
        self.max_tokens = max_tokens
        self.effort = effort or os.environ.get("TENDERRADAR_EFFORT", DEFAULT_EFFORT)
        self._client = client

    @property
    def params(self) -> dict:
        return {"effort": self.effort, "max_tokens": self.max_tokens}

    @property
    def client(self):
        if self._client is None:
            import anthropic

            key = resolve_api_key()
            # Without a key the SDK falls back to its own credential resolution.
            self._client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        return self._client

    def structured(self, *, system: str, user: str, schema: type[T]) -> tuple[T, CallStats]:
        import anthropic

        with self._client_lock:
            client = self.client
        try:
            with self._slots:
                started = time.perf_counter()
                response = client.messages.parse(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    output_format=schema,
                    output_config={"effort": self.effort},
                )
                latency = time.perf_counter() - started
        except anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic rejected the credentials (check ANTHROPIC_API_KEY).") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Rate limited by the Anthropic API after retries.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Could not reach the Anthropic API.") from exc
        except anthropic.AnthropicError as exc:  # for example no credentials configured
            raise LLMError(f"Anthropic client error: {exc}") from exc
        except ValidationError as exc:
            # The SDK validates the reply against the schema while parsing. A refusal
            # with text, or JSON cut off at max_tokens, fails here.
            raise LLMError(
                f"Model reply did not fit the schema ({exc.error_count()} errors)."
            ) from exc

        if response.stop_reason == "refusal":
            raise LLMError("The model declined this request.")
        if response.parsed_output is None:
            raise LLMError(f"No structured output (stop_reason={response.stop_reason}).")
        stats = CallStats(
            model=getattr(response, "model", None) or self.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_s=latency,
        )
        return response.parsed_output, stats


class CassetteProvider:
    """Replay recorded responses; optionally ask `inner` on a miss and record the answer.

    The key covers everything that shapes the answer: model, request parameters, both
    prompts and the full JSON schema. Change any of them and the old recording is a
    miss, never a stale answer. The file is JSON lines and only ever appended to, so an
    interrupted run keeps every response already paid for.
    """

    def __init__(
        self,
        path: Path,
        model: str,
        inner: LLMProvider | None = None,
        record: bool = False,
        params: dict | None = None,
    ):
        self.path = Path(path)
        self.model = model
        self.inner = inner
        self.record = record
        self.params = params or {}
        self.hits = 0
        self.misses = 0
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}
        if self.path.exists():
            with open(self.path, encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        entry = json.loads(line)
                        self._entries[entry["key"]] = entry

    def __len__(self) -> int:
        return len(self._entries)

    def key(self, system: str, user: str, schema: type[BaseModel]) -> str:
        payload = json.dumps(
            [self.model, self.params, system, user, schema.model_json_schema()],
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def has(self, system: str, user: str, schema: type[BaseModel]) -> bool:
        return self.key(system, user, schema) in self._entries

    def structured(self, *, system: str, user: str, schema: type[T]) -> tuple[T, CallStats]:
        key = self.key(system, user, schema)
        with self._lock:
            entry = self._entries.get(key)
        if entry is not None:
            self.hits += 1
            return schema.model_validate(entry["output"]), CallStats(
                **entry["stats"], replayed=True
            )
        self.misses += 1
        if self.inner is None:
            raise CassetteMiss(
                f"No recorded response in {self.path.name} and live calls are off. "
                "Run with --live (needs ANTHROPIC_API_KEY); add --record to save responses."
            )
        parsed, stats = self.inner.structured(system=system, user=user, schema=schema)
        if self.record:
            entry = {
                "key": key,
                "schema": schema.__name__,
                "output": parsed.model_dump(mode="json"),
                "stats": {
                    "model": stats.model,
                    "input_tokens": stats.input_tokens,
                    "output_tokens": stats.output_tokens,
                    "latency_s": round(stats.latency_s, 3),
                },
            }
            with self._lock:
                self._entries[key] = entry
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        return parsed, stats
