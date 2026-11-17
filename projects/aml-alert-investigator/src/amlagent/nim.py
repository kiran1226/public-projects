"""A second model vendor behind the same interface: NVIDIA-hosted models.

The NVIDIA API catalog exposes an OpenAI-compatible chat endpoint, so this module
translates the project's vendor-neutral step format to chat-completions and back.
Nothing above `llm.LLMProvider` knows which vendor answered.

The endpoint does not guarantee that tool arguments match the schema, and structured
output differs by backend, so this provider relies on neither: a requested schema is
stated in the prompt and callers validate what comes back.
"""

from __future__ import annotations

import json
import threading
import time

from .llm import BudgetExceeded, LLMError, StepRequest, StepResponse, Usage, resolve_key

BASE_URL = "https://integrate.api.nvidia.com/v1"
STOP_REASONS = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens"}
SCHEMA_NOTE = "\n\nReply with one JSON object and nothing else. It must match this JSON schema:\n"


def _messages(request: StepRequest) -> list[dict]:
    system = request.system
    if request.output_schema:
        system += SCHEMA_NOTE + json.dumps(request.output_schema)
    out: list[dict] = [{"role": "system", "content": system}]
    for message in request.messages:
        content = message["content"]
        if isinstance(content, str):
            out.append({"role": message["role"], "content": content})
        elif message["role"] == "assistant":
            words = " ".join(b["text"] for b in content if b.get("type") == "text")
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b["input"])},
                }
                for b in content
                if b.get("type") == "tool_use"
            ]
            turn: dict = {"role": "assistant", "content": words or None}
            if calls:
                turn["tool_calls"] = calls
            out.append(turn)  # thinking blocks are an Anthropic concept and are dropped
        else:
            for block in content:
                if block.get("type") == "tool_result":
                    body = block.get("content", "")
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": block["tool_use_id"],
                            "content": body if isinstance(body, str) else json.dumps(body),
                        }
                    )
            words = "\n".join(b["text"] for b in content if b.get("type") == "text")
            if words:
                out.append({"role": "user", "content": words})
    return out


def to_chat(request: StepRequest) -> dict:
    kwargs: dict = {
        "model": request.model,
        "messages": _messages(request),
        "max_tokens": request.max_tokens,
    }
    if request.tools:
        kwargs["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in request.tools
        ]
        kwargs["tool_choice"] = "auto"
    return kwargs


def _arguments(raw) -> dict:
    try:
        parsed = json.loads(raw or "")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def from_chat(completion, model: str, latency_s: float) -> StepResponse:
    choice = completion.choices[0]
    content: list[dict] = []
    if choice.message.content:
        content.append({"type": "text", "text": choice.message.content})
    for call in choice.message.tool_calls or []:
        content.append(
            {
                "type": "tool_use",
                "id": call.id,
                "name": call.function.name,
                "input": _arguments(call.function.arguments),
            }
        )
    usage = getattr(completion, "usage", None)
    return StepResponse(
        content=content,
        stop_reason=STOP_REASONS.get(choice.finish_reason, choice.finish_reason or ""),
        model=model,
        usage=Usage(
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        ),
        latency_s=latency_s,
    )


class NvidiaProvider:
    """NVIDIA API catalog through the OpenAI-compatible client.

    The trial tier is rate-limited and not priced per token, so the guard here is a
    request cap and a minimum interval between calls, not a dollar budget.
    """

    def __init__(self, client=None, min_interval_s: float = 1.6, max_requests: int = 600):
        self._client = client
        self.min_interval_s, self.max_requests = min_interval_s, max_requests
        self._lock = threading.Lock()
        self._count, self._next = 0, 0.0

    @property
    def client(self):
        if self._client is None:
            key = resolve_key("NVIDIA_API_KEY")
            if not key:
                raise LLMError("NVIDIA_API_KEY is not set (add it to .env).")
            from openai import OpenAI

            self._client = OpenAI(base_url=BASE_URL, api_key=key)
        return self._client

    def step(self, request: StepRequest) -> StepResponse:
        with self._lock:  # one request at a time keeps the run under the account rate limit
            if self._count >= self.max_requests:
                raise BudgetExceeded(f"NVIDIA request cap of {self.max_requests} reached.")
            self._count += 1
            wait = self._next - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next = time.monotonic() + self.min_interval_s
            client = self.client
            started = time.perf_counter()
            try:
                completion = client.chat.completions.create(**to_chat(request))
            except Exception as exc:  # any client or endpoint failure routes the case to a person
                raise LLMError(f"NVIDIA call failed: {type(exc).__name__}: {exc}") from exc
            return from_chat(completion, request.model, round(time.perf_counter() - started, 3))
