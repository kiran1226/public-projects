import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from revwatch.llm import (
    AnthropicProvider,
    CappedProvider,
    CassetteMiss,
    CassetteProvider,
    LLMError,
    ScriptedProvider,
    SpendLimit,
    StepRequest,
    StepResponse,
    Usage,
    cost_usd,
    priced_as,
)

TOOLS = [{"name": "calendar", "description": "d", "input_schema": {"type": "object"}}]


def request(**changes: Any) -> StepRequest:
    base: dict[str, Any] = {
        "model": "claude-haiku-5-5", "effort": "low", "system": "You investigate.",
        "messages": [{"role": "user", "content": "Alert INC-1"}], "tools": TOOLS, "tag": "INC-1",
    }  # fmt: skip
    return StepRequest(**{**base, **changes})


def answer(text: str = "ok") -> StepResponse:
    return StepResponse(
        content=[{"type": "text", "text": text}], stop_reason="end_turn", model="claude-haiku-5-5",
        usage=Usage(input_tokens=1000, output_tokens=200), latency_s=1.5,
    )  # fmt: skip


def test_the_key_covers_everything_that_shapes_the_answer_and_not_the_tag() -> None:
    base = request().key()
    assert request(tag="INC-2").key() == base
    for changed in (
        request(model="claude-sonnet-5-5"),
        request(effort="medium"),
        request(system="You investigate carefully."),
        request(tools=[{**TOOLS[0], "description": "changed"}]),
        request(messages=[{"role": "user", "content": "Alert INC-1 "}]),
        request(max_tokens=8000),
    ):
        assert changed.key() != base


def test_a_recorded_step_replays_without_a_live_provider(tmp_path: Path) -> None:
    path = tmp_path / "cassettes" / "agent.json"
    live = ScriptedProvider([answer("first")])
    recording = CassetteProvider(path, inner=live, record=True)
    assert recording.step(request()).content[0]["text"] == "first"
    replay = CassetteProvider(path)
    again = replay.step(request(tag="another tag"))
    assert again == answer("first") and len(replay) == 1 and len(live.requests) == 1
    assert not path.with_suffix(".tmp").exists()
    stored = json.loads(path.read_text())
    assert next(iter(stored.values()))["tag"] == "INC-1"


def test_a_changed_prompt_is_a_miss_and_never_a_stale_answer(tmp_path: Path) -> None:
    path = tmp_path / "agent.json"
    CassetteProvider(path, inner=ScriptedProvider([answer()]), record=True).step(request())
    with pytest.raises(CassetteMiss, match="live calls are off.*--live"):
        CassetteProvider(path).step(request(system="A new prompt."))
    with pytest.raises(CassetteMiss, match="INC-1"):
        CassetteProvider(tmp_path / "empty.json").step(request())


def test_without_record_nothing_is_written(tmp_path: Path) -> None:
    path = tmp_path / "agent.json"
    CassetteProvider(path, inner=ScriptedProvider([answer()])).step(request())
    assert not path.exists()


def test_a_recorded_failure_replays_as_a_failure_and_is_retried_live(tmp_path: Path) -> None:
    path = tmp_path / "agent.json"
    failing = CassetteProvider(
        path, inner=ScriptedProvider([LLMError("rate limited")]), record=True
    )
    with pytest.raises(LLMError, match="rate limited"):
        failing.step(request())
    with pytest.raises(LLMError, match="rate limited"):
        CassetteProvider(path).step(request())  # the same run, replayed
    retried = CassetteProvider(path, inner=ScriptedProvider([answer("second try")]), record=True)
    assert retried.step(request()).content[0]["text"] == "second try"
    assert CassetteProvider(path).step(request()).content[0]["text"] == "second try"


def test_live_calls_stop_at_the_spend_limit() -> None:
    inner = ScriptedProvider([answer(), answer(), answer()])
    capped = CappedProvider(inner, limit_usd=0.0003)  # one call costs US$0.0002
    capped.step(request())
    capped.step(request(system="Another prompt."))
    assert capped.spent_usd == pytest.approx(0.0004)  # past the limit by at most one call
    with pytest.raises(SpendLimit, match="--max-usd"):
        capped.step(request(system="A third prompt."))
    assert len(inner.requests) == 2  # once the limit is reached nothing is sent


def test_a_call_the_limit_stopped_is_not_recorded_and_replays_cost_nothing(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    stopped = CassetteProvider(path, CappedProvider(ScriptedProvider([answer()]), 0.0), record=True)
    with pytest.raises(SpendLimit):
        stopped.step(request())
    assert not path.exists()  # not saved as a failure: the next run asks the model
    CassetteProvider(path, ScriptedProvider([answer()]), record=True).step(request())
    replay = CassetteProvider(path, CappedProvider(ScriptedProvider([]), 0.0))
    assert replay.step(request()) == answer()  # a recorded step never reaches the limit


def test_cost_comes_from_recorded_usage_and_list_prices() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost_usd("claude-haiku-5-5", usage) == pytest.approx(0.60)
    cached = Usage(
        input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000, cache_write_tokens=1_000_000
    )
    assert cost_usd("claude-haiku-5-5", cached) == pytest.approx(0.01 + 0.125)
    assert priced_as("claude-haiku-5-5-20261001") == "claude-haiku-5-5"
    # A model that is not in the table is never free: it is priced as the dearest one.
    assert priced_as("claude-next") == "claude-opus-5-5"
    assert cost_usd("claude-next", usage) == pytest.approx(24.0)


class FakeBlock:
    def __init__(self, **fields: Any) -> None:
        self.fields = fields

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return dict(self.fields)


class FakeMessages:
    def __init__(self, outcome: Any) -> None:
        self.outcome, self.calls = outcome, []  # type: ignore[var-annotated]

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_the_claude_provider_sends_the_request_and_reads_usage() -> None:
    reply = SimpleNamespace(
        content=[FakeBlock(type="thinking", thinking="", signature="sig"), FakeBlock(type="tool_use", id="tu_1", name="calendar", input={})],
        stop_reason="tool_use", model="claude-haiku-5-5",
        usage=SimpleNamespace(input_tokens=900, output_tokens=120, cache_read_input_tokens=700, cache_creation_input_tokens=None),
    )  # fmt: skip
    messages = FakeMessages(reply)
    response = AnthropicProvider(client=SimpleNamespace(messages=messages)).step(request())
    sent = messages.calls[0]
    assert sent["model"] == "claude-haiku-5-5" and sent["output_config"] == {"effort": "low"}
    assert sent["system"] == [
        {"type": "text", "text": "You investigate.", "cache_control": {"type": "ephemeral"}}
    ]
    assert sent["tools"] == TOOLS and sent["messages"] == [
        {"role": "user", "content": "Alert INC-1"}
    ]
    assert (
        "temperature" not in sent and "thinking" not in sent
    )  # current models reject both settings
    # Blocks come back whole, so that they can be sent again unchanged.
    assert response.content[0] == {"type": "thinking", "thinking": "", "signature": "sig"}
    assert response.stop_reason == "tool_use"
    assert response.usage == Usage(input_tokens=900, output_tokens=120, cache_read_tokens=700)


def test_an_sdk_error_becomes_one_kind_of_error_for_the_loop() -> None:
    client = SimpleNamespace(messages=FakeMessages(TimeoutError("read timed out")))
    with pytest.raises(LLMError, match="TimeoutError: read timed out"):
        AnthropicProvider(client=client).step(request())
