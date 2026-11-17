"""A bounded tool loop. The model chooses the next tool; code bounds everything else.

The conversation is append-only: assistant content goes back exactly as received
(thinking blocks included), and nothing already sent is edited.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .llm import (
    BudgetExceeded,
    CassetteMiss,
    LeakBlocked,
    LLMError,
    LLMProvider,
    StepRequest,
    cost_usd,
)
from .models import Step, ToolCall


@dataclass(frozen=True)
class RoleConfig:
    name: str
    model: str
    effort: str
    system: str
    tools: list[dict]
    submit_tool: str
    max_steps: int
    max_tokens: int


@dataclass
class LoopResult:
    submission: Any | None
    steps: list[Step] = field(default_factory=list)
    failure: str | None = None


def _result(block: dict, output: str, is_error: bool) -> dict:
    result = {"type": "tool_result", "tool_use_id": block["id"], "content": output}
    return result | {"is_error": True} if is_error else result


def run_loop(
    provider: LLMProvider,
    role: RoleConfig,
    brief: str,
    execute: Callable[[str, dict], tuple[str, bool]],
    parse: Callable[[dict], Any],
    *,
    tag: str,
    cost_left: Callable[[], float] | None = None,
    on_step: Callable[[Step], None] | None = None,
) -> LoopResult:
    messages: list[dict] = [{"role": "user", "content": brief}]
    steps: list[Step] = []
    nudged = retried = False

    for number in range(1, role.max_steps + 1):
        request = StepRequest(
            model=role.model,
            effort=role.effort,
            system=role.system,
            messages=messages,
            tools=role.tools,
            max_tokens=role.max_tokens,
            tag=tag,
        )
        try:
            response = provider.step(request)
        except (CassetteMiss, BudgetExceeded):
            raise  # these stop the whole run, not one case
        except LeakBlocked:
            return LoopResult(None, steps, "leak_blocked")
        except LLMError as exc:
            return LoopResult(None, steps, f"model_failure: {exc}")

        step = Step(
            role=role.name,
            model=response.model,
            cost_usd=cost_usd(response.model, response.usage),
            latency_s=response.latency_s,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=response.usage.cache_read_tokens,
            cache_write_tokens=response.usage.cache_write_tokens,
        )
        steps.append(step)
        if on_step is not None:
            on_step(step)
        if response.stop_reason in ("refusal", "max_tokens"):
            return LoopResult(None, steps, f"model_failure: {response.stop_reason}")

        messages.append({"role": "assistant", "content": response.content})  # unchanged
        calls = [b for b in response.content if b.get("type") == "tool_use"]
        submit = next((b for b in calls if b["name"] == role.submit_tool), None)

        if submit is not None:
            try:
                return LoopResult(parse(submit["input"]), steps)
            except ValueError as exc:  # includes pydantic.ValidationError
                if retried:
                    return LoopResult(None, steps, "model_failure: invalid submission")
                retried = True
                # Every tool_use needs a result; calls made alongside a submit are not run.
                results = [
                    _result(
                        b,
                        f"Invalid submission: {exc}"
                        if b is submit
                        else "Not run: submit must be called on its own.",
                        True,
                    )
                    for b in calls
                ]
                messages.append({"role": "user", "content": results})
                continue

        if not calls:
            if nudged:
                return LoopResult(None, steps, "model_failure: no submit call")
            nudged = True
            messages.append(
                {
                    "role": "user",
                    "content": f"You have not called {role.submit_tool}. "
                    "Call it now with your conclusion.",
                }
            )
            continue

        # In call order, not in parallel: tools issue tokens, and a fixed order keeps
        # token numbers (and so recorded replays) deterministic.
        outputs = [execute(b["name"], b["input"]) for b in calls]
        step.tool_calls = [
            ToolCall(name=b["name"], input=b["input"], output=out, is_error=err)
            for b, (out, err) in zip(calls, outputs, strict=True)
        ]
        content: list[dict] = [
            _result(b, out, err) for b, (out, err) in zip(calls, outputs, strict=True)
        ]
        if cost_left is not None and cost_left() <= 0:
            return LoopResult(None, steps, "limit")
        if number == role.max_steps - 1:
            content.append(
                {
                    "type": "text",
                    "text": f"One step left. Call {role.submit_tool} now with what you have.",
                }
            )
        messages.append({"role": "user", "content": content})

    return LoopResult(None, steps, "limit")
