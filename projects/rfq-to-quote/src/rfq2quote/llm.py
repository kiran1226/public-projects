"""Thin LLM interface: one method, structured output in, typed object out.

Everything above this file depends on `LLMProvider`, not on a vendor SDK, so a
different backend (for example an NVIDIA NIM endpoint) is one new class.

`CassetteProvider` records responses to disk and replays them, which makes the
demo, the tests and CI deterministic and free once a run has been recorded.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# Chosen by measurement: on the labelled sets this model had no unsafe auto-approval
# and held every quote policy requires, at about a tenth of a cent per request.
DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_EFFORT = "medium"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept the server-side `fallbacks` parameter.
FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2"})
# USD per million tokens (input, output). Anthropic list prices as of 2026-11.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-2": (8.00, 40.00),
}


API_KEY_VARS = ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "claude_api_key")


def resolve_api_key() -> str | None:
    """Find an API key in the environment or in a .env file in this or a parent directory.

    `ANTHROPIC_API_KEY` is the standard name; the other spellings are accepted as aliases.
    Real environment variables win over the .env file.
    """
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
    for name in API_KEY_VARS:
        if os.environ.get(name):
            return os.environ[name]
    return None


class LLMError(RuntimeError):
    """The model call failed or returned nothing usable. Callers route to a human."""


class CassetteMiss(LLMError):
    """No recorded response for this request and no live provider to ask."""


@dataclass(frozen=True)
class CallStats:
    model: str
    input_tokens: int
    output_tokens: int
    latency_s: float

    @property
    def cost_usd(self) -> float:
        # A model with no listed price is costed at the dearest one, so a reply served
        # by an unexpected model still counts against a spend cap.
        price_in, price_out = PRICES.get(self.model) or max(PRICES.values())
        return (self.input_tokens * price_in + self.output_tokens * price_out) / 1_000_000


class LLMProvider(Protocol):
    model: str

    def structured(self, *, system: str, user: str, schema: type[T]) -> tuple[T, CallStats]: ...


class AnthropicProvider:
    """Claude via the official SDK, using structured outputs."""

    def __init__(
        self,
        model: str | None = None,
        client=None,
        max_tokens: int = 16000,
        effort: str | None = None,
        concurrency: int = 8,
    ):
        # At most `concurrency` requests are in flight, however many threads call in.
        self._slots = threading.BoundedSemaphore(max(1, concurrency))
        self._client_lock = threading.Lock()
        self.model = model or os.environ.get("RFQ2QUOTE_MODEL", DEFAULT_MODEL)
        self.max_tokens = max_tokens
        # Reasoning effort is set explicitly so cost does not depend on a model default.
        self.effort = effort or os.environ.get("RFQ2QUOTE_EFFORT", DEFAULT_EFFORT)
        self._client = client

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

        request = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            output_config={"effort": self.effort},
        )
        with self._client_lock:
            client = self.client
        try:
            with self._slots:
                started = time.perf_counter()
                if self.model in FALLBACK_MODELS:
                    # If a safety classifier declines, the API retries on a fallback model.
                    response = client.beta.messages.parse(
                        **request, betas=[FALLBACK_BETA], fallbacks="default"
                    )
                else:
                    response = client.messages.parse(**request)
                latency = time.perf_counter() - started
        except anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic rejected the credentials (check ANTHROPIC_API_KEY).") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Rate limited by the Anthropic API after retries.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Could not reach the Anthropic API.") from exc
        except anthropic.AnthropicError as exc:  # e.g. no credentials configured
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
            # The model that answered: differs from self.model if a fallback served it.
            model=getattr(response, "model", None) or self.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_s=latency,
        )
        return response.parsed_output, stats


class CassetteProvider:
    """Replay recorded responses; optionally ask `inner` on a miss and record the answer.

    The key covers everything that shapes the answer: model, request parameters,
    both prompts and the full JSON schema. Change any of them and the old recording
    is a miss, never a stale answer.

    `also` names further recordings that are only read. A request that `path` does not
    hold is looked up there; nothing is ever written to them or copied out of them.
    """

    def __init__(
        self,
        path: Path,
        model: str,
        inner: LLMProvider | None = None,
        record: bool = False,
        params: dict | None = None,
        scrub: Callable[[dict], dict] | None = None,
        also: Iterable[Path] = (),
    ):
        self.path = path
        self.model = model
        self.inner = inner
        self.record = record
        self.params = params or {}
        # Applied to an output before it is saved, for recordings that should not keep
        # free text (for example the model quoting third-party data in a reason).
        self.scrub = scrub
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )
        self._read_only: dict[str, dict] = {}
        for extra in also:
            if extra.exists():
                self._read_only |= json.loads(extra.read_text(encoding="utf-8"))

    def _key(self, system: str, user: str, schema: type[BaseModel]) -> str:
        payload = json.dumps(
            [self.model, self.params, system, user, schema.model_json_schema()],
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def structured(self, *, system: str, user: str, schema: type[T]) -> tuple[T, CallStats]:
        key = self._key(system, user, schema)
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:
            entry = self._read_only.get(key)
        if entry is not None:
            return schema.model_validate(entry["output"]), CallStats(**entry["stats"])
        if self.inner is None:
            raise CassetteMiss(
                f"No recorded response in {self.path.name} and live calls are off. "
                "Run with --live (needs ANTHROPIC_API_KEY); add --record to save responses."
            )
        parsed, stats = self.inner.structured(system=system, user=user, schema=schema)
        if self.record:
            output = parsed.model_dump(mode="json")
            with self._lock:
                self._entries[key] = {
                    "schema": schema.__name__,
                    "output": self.scrub(output) if self.scrub else output,
                    "stats": {
                        "model": stats.model,
                        "input_tokens": stats.input_tokens,
                        "output_tokens": stats.output_tokens,
                        "latency_s": round(stats.latency_s, 3),
                    },
                }
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # Write to a temp file and rename, so an interrupted run cannot corrupt
                # the recordings already paid for.
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(
                    json.dumps(self._entries, indent=1, ensure_ascii=False, sort_keys=True),
                    encoding="utf-8",
                )
                os.replace(tmp, self.path)
        return parsed, stats
