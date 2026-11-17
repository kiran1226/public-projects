"""The agent investigator: a bounded tool loop. The model chooses the next tool;
code bounds everything else.

The conversation is append-only. Assistant content goes back exactly as it was
received, thinking blocks included, and nothing already sent is edited.
"""

from __future__ import annotations

import hashlib
from importlib import resources
from typing import Any

from pydantic import ValidationError

from revwatch.findings import Finding
from revwatch.investigate.base import Case, Investigation, StopRun, no_finding
from revwatch.llm import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    CassetteMiss,
    LLMError,
    LLMProvider,
    SpendLimit,
    StepRequest,
    cost_usd,
)
from revwatch.policy import ReviewPolicy
from revwatch.tools import SUBMIT, TOOL_DEFINITIONS, Toolbox

PROMPT_VERSION = "investigator_v1"
EXTRA_STEPS = 4  # model steps allowed beyond one per tool call: nudges, a retry, the submit


def load_prompt(version: str = PROMPT_VERSION) -> str:
    return resources.files("revwatch.prompts").joinpath(f"{version}.md").read_text("utf-8")


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]


def brief(case: Case, toolbox: Toolbox) -> str:
    """The first message: what the detector saw. Everything else the agent looks up."""
    hour = toolbox.warehouse.hour(case.alert_tick)
    now = toolbox.warehouse.hour(case.now)
    where = ", ".join(f"{dim}={value}" for dim, value in case.slice.items) or "the whole business"
    side = "above" if case.direction > 0 else "below"
    return (
        f"Alert {case.incident_id}\n"
        f"Metric: {case.metric}\n"
        f"Slice: {where}\n"
        f"Direction: {side} expected\n"
        f"Alert hour: {hour:%Y-%m-%dT%H:%M} "
        "(out of range for at least two of the last three hours)\n"
        f"Current time: {now:%Y-%m-%dT%H:%M}. Data is complete up to this hour; no window can "
        "end later.\n\n"
        "Investigate and submit your finding."
    )


def _result(block: dict[str, Any], text: str, is_error: bool = False) -> dict[str, Any]:
    result = {"type": "tool_result", "tool_use_id": block["id"], "content": text}
    return {**result, "is_error": True} if is_error else result


class AgentInvestigator:
    name = "agent"

    def __init__(
        self,
        provider: LLMProvider,
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
        policy: ReviewPolicy | None = None,
        prompt: str | None = None,
    ) -> None:
        self.provider = provider
        self.model, self.effort = model, effort
        self.policy = policy or ReviewPolicy()
        self.prompt = prompt if prompt is not None else load_prompt()

    def investigate(self, case: Case, toolbox: Toolbox) -> Investigation:
        budget = self.policy.tool_budget
        messages: list[dict[str, Any]] = [{"role": "user", "content": brief(case, toolbox)}]
        result = Investigation(no_finding("No finding was submitted."), toolbox.trace)
        result.model = self.model
        nudged = retried = False

        def failed(flag: str, detail: str) -> Investigation:
            result.flags, result.detail = [flag], detail
            return result

        for _ in range(budget + EXTRA_STEPS):
            request = StepRequest(
                model=self.model,
                effort=self.effort,
                system=self.prompt,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tag=case.incident_id,
            )
            try:
                response = self.provider.step(request)
            except (CassetteMiss, SpendLimit) as exc:
                raise StopRun(str(exc)) from exc  # this stops the run, not one incident
            except LLMError as exc:
                return failed("INVESTIGATION_ERROR", str(exc))
            result.model_calls += 1
            result.cost_usd += cost_usd(response.model, response.usage)
            result.model_seconds += response.latency_s
            if response.stop_reason in ("refusal", "max_tokens"):
                return failed("INVESTIGATION_ERROR", f"the model stopped: {response.stop_reason}")

            messages.append({"role": "assistant", "content": response.content})  # unchanged
            calls = [block for block in response.content if block.get("type") == "tool_use"]
            submit = next((block for block in calls if block["name"] == SUBMIT), None)

            if submit is not None:
                try:
                    result.finding = Finding.model_validate(submit["input"])
                    return result
                except ValidationError as exc:
                    if retried:
                        return failed("INVESTIGATION_ERROR", "the finding did not fit the schema")
                    retried = True
                    # Every tool_use needs a result; calls made alongside a submit are not run.
                    problems = "; ".join(
                        f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}"
                        for error in exc.errors()
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                _result(
                                    block,
                                    f"Invalid finding: {problems}. Submit it again, corrected."
                                    if block is submit
                                    else "Not run: submit_finding must be called on its own.",
                                    True,
                                )
                                for block in calls
                            ],
                        }
                    )
                    continue

            if not calls:
                if nudged:
                    return failed("INVESTIGATION_ERROR", "the model did not submit a finding")
                nudged = True
                messages.append(
                    {
                        "role": "user",
                        "content": f"You have not called {SUBMIT}. Call it now with your finding.",
                    }
                )
                continue

            if len(toolbox.trace) + len(calls) > budget:
                return failed(
                    "BUDGET_EXHAUSTED",
                    f"{len(toolbox.trace)} tool calls made, {len(calls)} more asked for; "
                    f"the budget is {budget}",
                )
            # In call order: the call ids (c1, c2, ...) are part of what is recorded.
            content: list[dict[str, Any]] = []
            for block in calls:
                outcome = toolbox.call(block["name"], block["input"])
                content.append(_result(block, outcome.text(), outcome.error is not None))
            left = budget - len(toolbox.trace)
            if left <= 2:
                content.append(
                    {
                        "type": "text",
                        "text": f"{left} tool call(s) left. After that, call {SUBMIT} with what "
                        "you have.",
                    }
                )
            messages.append({"role": "user", "content": content})

        return failed("BUDGET_EXHAUSTED", "the model did not finish within the step limit")
