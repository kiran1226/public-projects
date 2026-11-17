import os
from types import SimpleNamespace

import pytest

from amlagent.llm import (
    PRICES,
    AnthropicProvider,
    CassetteMiss,
    CassetteProvider,
    GuardedProvider,
    LeakBlocked,
    LLMError,
    RouterProvider,
    ScriptedProvider,
    StepRequest,
    Usage,
    cost_usd,
    is_metered,
)
from amlagent.privacy import OutboundScanner
from conftest import reply, text


def request(content="hello", **over):
    base = dict(
        model="claude-haiku-5-5",
        system="You are a reviewer.",
        messages=[{"role": "user", "content": content}],
        tag="AL-001/triage",
    )
    return StepRequest(**base | over)


def test_key_ignores_the_tag_and_follows_the_prompt():
    assert request().key() == request(tag="other").key()
    assert request().key() != request(content="different").key()
    assert request().key() != request(effort="high").key()


def test_cassette_records_then_replays_without_a_live_provider(tmp_path):
    path = tmp_path / "c.json"
    live = ScriptedProvider([reply(text("first"), stop="end_turn")])
    assert CassetteProvider(path, inner=live, record=True).step(request()).content == [
        text("first")
    ]
    replay = CassetteProvider(path)
    assert replay.step(request()).content == [text("first")]
    with pytest.raises(CassetteMiss):
        replay.step(request(content="never recorded"))


def test_cassette_stores_what_was_sent_once(tmp_path):
    path = tmp_path / "c.json"
    live = ScriptedProvider([reply(text("a")), reply(text("b"))])
    cassette = CassetteProvider(path, inner=live, record=True)
    cassette.step(request("brief"))
    cassette.step(
        request(
            messages=[
                {"role": "user", "content": "brief"},
                {"role": "assistant", "content": [text("a")]},
                {"role": "user", "content": "tool output"},
            ]
        )
    )
    sent = dict(CassetteProvider(path).sent())
    joined = "\n".join(t for _, t in CassetteProvider(path).sent())
    assert "AL-001/triage" in sent
    assert joined.count("You are a reviewer.") == 1 and joined.count("brief") == 1
    assert "tool output" in joined


def test_interrupted_write_keeps_earlier_recordings(tmp_path, monkeypatch):
    path = tmp_path / "c.json"
    live = ScriptedProvider([reply(text("kept")), reply(text("lost"))])
    cassette = CassetteProvider(path, inner=live, record=True)
    cassette.step(request("one"))
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        cassette.step(request("two"))
    monkeypatch.undo()
    assert CassetteProvider(path).step(request("one")).content == [text("kept")]


def test_guarded_provider_blocks_before_anything_is_sent():
    inner = ScriptedProvider([reply(text("x"))])
    guarded = GuardedProvider(inner, OutboundScanner({"Anna Becker"}))
    with pytest.raises(LeakBlocked) as blocked:
        guarded.step(request("the customer is anna becker"))
    assert blocked.value.kinds == ["known_identifier"] and inner.requests == []
    assert guarded.step(request("CUST_01 deposited cash")).content == [text("x")]


def test_cost_arithmetic():
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )
    assert cost_usd("claude-opus-5-5", usage) == pytest.approx(4.00 + 20.00 + 0.20 + 5.00)
    assert cost_usd(
        "claude-haiku-5-5", Usage(input_tokens=27_000, output_tokens=2_000)
    ) == pytest.approx(0.0037)
    assert cost_usd("nvidia/some-model", usage) == 0.0 and not is_metered("nvidia/some-model")
    # An unknown Claude model is priced at the most expensive known rate, never as free.
    assert cost_usd("claude-unknown", usage) == max(cost_usd(m, usage) for m in PRICES)
    assert cost_usd("claude-opus-4-8", usage) == pytest.approx(5.00 + 25.00 + 0.50 + 6.25)
    # A dated id from the API is priced as the model it names.
    assert cost_usd("claude-haiku-5-5-20261001", usage) == cost_usd("claude-haiku-5-5", usage)


def test_router_sends_each_model_to_its_provider():
    claude, nim = ScriptedProvider([reply(text("c"))]), ScriptedProvider([reply(text("n"))])
    router = RouterProvider(anthropic=claude, nvidia=nim)
    assert router.step(request()).content == [text("c")]
    assert router.step(request(model="nvidia/some-model")).content == [text("n")]
    with pytest.raises(LLMError):
        RouterProvider(anthropic=claude).step(request(model="nvidia/some-model"))


class FakeBlock(SimpleNamespace):
    def model_dump(self, **kwargs):
        return {k: v for k, v in vars(self).items() if v is not None}


def fake_client(calls):
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            content=[FakeBlock(type="text", text="done", citations=None)],
            stop_reason="end_turn",
            model=kwargs["model"],
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=5,
                cache_read_input_tokens=3,
                cache_creation_input_tokens=None,
            ),
        )

    messages = SimpleNamespace(create=create)
    return SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages))


def test_anthropic_request_shape():
    calls = []
    provider = AnthropicProvider(client=fake_client(calls))
    tools = [{"name": "t", "description": "d", "strict": True, "input_schema": {"type": "object"}}]
    response = provider.step(request(effort="low", tools=tools))
    sent = calls[0]
    assert sent["output_config"] == {"effort": "low"} and sent["tools"] == tools
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert not {"thinking", "temperature", "tool_choice", "betas"} & set(sent)
    assert response.content == [{"type": "text", "text": "done"}]
    assert response.usage == Usage(input_tokens=10, output_tokens=5, cache_read_tokens=3)

    provider.step(request(model="claude-opus-5-5", output_schema={"type": "object"}))
    opus = calls[1]
    assert opus["betas"] == ["server-side-fallback-2026-07-01"] and opus["fallbacks"] == "default"
    assert opus["output_config"]["format"] == {"type": "json_schema", "schema": {"type": "object"}}
    assert "tools" not in opus


def test_anthropic_errors_become_llm_errors():
    def boom(**kwargs):
        raise RuntimeError("network down")

    client = SimpleNamespace(messages=SimpleNamespace(create=boom))
    with pytest.raises(LLMError):
        AnthropicProvider(client=client).step(request())


def test_a_failed_live_step_is_recorded_and_replays_as_the_same_failure(tmp_path):
    path = tmp_path / "c.json"
    live = ScriptedProvider([LLMError("429 rate limited"), reply(text("second try"))])
    with pytest.raises(LLMError, match="429"):
        CassetteProvider(path, inner=live, record=True).step(request())
    with pytest.raises(LLMError, match="429"):  # replay gives the same failure, not a miss
        CassetteProvider(path).step(request())
    assert len(CassetteProvider(path).sent()) == 1  # what was sent is still there for the leak scan
    # With a live provider attached, a recorded failure is retried and replaced.
    assert CassetteProvider(path, inner=live, record=True).step(request()).content == [
        text("second try")
    ]
    assert CassetteProvider(path).step(request()).content == [text("second try")]


def test_run_level_errors_are_never_recorded(tmp_path):
    from amlagent.llm import BudgetExceeded

    path = tmp_path / "c.json"
    with pytest.raises(BudgetExceeded):
        CassetteProvider(path, inner=ScriptedProvider([BudgetExceeded("cap")]), record=True).step(
            request()
        )
    with pytest.raises(CassetteMiss):
        CassetteProvider(path).step(request())


def test_blocks_are_serialised_with_api_field_names():
    seen = {}

    class Block(SimpleNamespace):
        def model_dump(self, **kwargs):
            seen.update(kwargs)
            return {"type": "text", "text": "x"}

    def create(**kwargs):
        return SimpleNamespace(
            content=[Block()],
            stop_reason="end_turn",
            model=kwargs["model"],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    AnthropicProvider(client=client).step(request())
    assert (
        seen["by_alias"] is True and seen["exclude_none"] is True
    )  # e.g. a fallback block's `from`
