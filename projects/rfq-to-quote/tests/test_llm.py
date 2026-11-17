from types import SimpleNamespace

import pytest

from rfq2quote.llm import (
    PRICES,
    AnthropicProvider,
    CallStats,
    CassetteMiss,
    CassetteProvider,
    LLMError,
    resolve_api_key,
)
from rfq2quote.models import MatchDecision

DECISION = MatchDecision(sku="CAB-NYY-5X2.5", confidence="high", reason="exact type and size")


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def fake_client(response):
    messages = FakeMessages(response)
    return SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages)), messages


def response(parsed=DECISION, stop_reason="end_turn"):
    usage = SimpleNamespace(input_tokens=1200, output_tokens=300)
    return SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason, usage=usage)


def test_anthropic_provider_returns_parsed_object_and_stats():
    client, messages = fake_client(response())
    provider = AnthropicProvider(model="claude-opus-5-5", client=client)
    parsed, stats = provider.structured(system="sys", user="usr", schema=MatchDecision)
    assert parsed == DECISION
    assert (stats.input_tokens, stats.output_tokens) == (1200, 300)
    assert stats.cost_usd == pytest.approx((1200 * 4 + 300 * 20) / 1e6)
    assert messages.kwargs["output_format"] is MatchDecision
    assert messages.kwargs["fallbacks"] == "default"  # refusal fallback is on for this model
    assert messages.kwargs["output_config"] == {"effort": "medium"}  # explicit, not a default


def test_stats_record_the_model_that_answered():
    served = response()
    served.model = "claude-opus-4-8"  # a server-side fallback answered
    client, _ = fake_client(served)
    _, stats = AnthropicProvider(model="claude-opus-5-5", client=client).structured(
        system="s", user="u", schema=MatchDecision
    )
    assert stats.model == "claude-opus-4-8"


def test_reply_that_does_not_fit_the_schema_is_an_llm_error():
    class Truncating:
        def parse(self, **kwargs):
            # What the SDK's parse helper does with JSON cut off at max_tokens.
            MatchDecision.model_validate_json('{"sku": "CAB-NYY')

    broken = Truncating()
    client = SimpleNamespace(messages=broken, beta=SimpleNamespace(messages=broken))
    with pytest.raises(LLMError, match="did not fit the schema"):
        AnthropicProvider(model="claude-opus-5-5", client=client).structured(
            system="s", user="u", schema=MatchDecision
        )


def test_models_without_server_side_fallback_use_the_plain_endpoint():
    client, messages = fake_client(response())
    AnthropicProvider(model="claude-haiku-5-5", client=client).structured(
        system="s", user="u", schema=MatchDecision
    )
    assert "fallbacks" not in messages.kwargs


@pytest.mark.parametrize("bad", [response(stop_reason="refusal"), response(parsed=None)])
def test_refusal_or_empty_output_raises(bad):
    client, _ = fake_client(bad)
    with pytest.raises(LLMError):
        AnthropicProvider(model="claude-opus-5-5", client=client).structured(
            system="s", user="u", schema=MatchDecision
        )


class CountingProvider:
    model = "claude-opus-5-5"

    def __init__(self):
        self.calls = 0

    def structured(self, *, system, user, schema):
        self.calls += 1
        return DECISION, CallStats(self.model, 100, 20, 0.4)


def test_cassette_records_then_replays_without_calling_again(tmp_path):
    path = tmp_path / "cassette.json"
    inner = CountingProvider()
    recorder = CassetteProvider(path, inner.model, inner=inner, record=True)
    recorder.structured(system="s", user="u", schema=MatchDecision)
    recorder.structured(system="s", user="u", schema=MatchDecision)
    assert inner.calls == 1

    replay = CassetteProvider(path, inner.model)
    parsed, stats = replay.structured(system="s", user="u", schema=MatchDecision)
    assert parsed == DECISION and stats.input_tokens == 100


def test_cassette_scrub_changes_what_is_saved_not_what_the_live_call_returns(tmp_path):
    path = tmp_path / "cassette.json"
    inner = CountingProvider()
    recorder = CassetteProvider(
        path, inner.model, inner=inner, record=True, scrub=lambda out: {**out, "reason": ""}
    )
    live, _ = recorder.structured(system="s", user="u", schema=MatchDecision)
    assert live.reason == DECISION.reason

    replayed, _ = CassetteProvider(path, inner.model).structured(
        system="s", user="u", schema=MatchDecision
    )
    assert replayed.sku == DECISION.sku and replayed.reason == ""
    assert DECISION.reason not in path.read_text(encoding="utf-8")


def test_cassette_also_replays_recordings_it_never_writes_to(tmp_path):
    extra = tmp_path / "hard" / "cassette.json"
    CassetteProvider(extra, "claude-opus-5-5", inner=CountingProvider(), record=True).structured(
        system="s", user="from the extra recording", schema=MatchDecision
    )
    before = extra.read_bytes()

    main = tmp_path / "cassette.json"
    inner = CountingProvider()
    provider = CassetteProvider(main, inner.model, inner=inner, record=True, also=[extra])
    provider.structured(system="s", user="from the extra recording", schema=MatchDecision)
    assert inner.calls == 0  # answered from the extra recording
    provider.structured(system="s", user="new", schema=MatchDecision)
    assert inner.calls == 1

    assert extra.read_bytes() == before
    assert "from the extra recording" not in main.read_text(encoding="utf-8")
    # One saved entry: the extra recording's answers are not copied into the main one.
    assert len(__import__("json").loads(main.read_text(encoding="utf-8"))) == 1
    missing = CassetteProvider(main, inner.model, also=[tmp_path / "not-there.json"])
    with pytest.raises(CassetteMiss):
        missing.structured(system="s", user="from the extra recording", schema=MatchDecision)


def test_cassette_miss_without_live_provider(tmp_path):
    replay = CassetteProvider(tmp_path / "none.json", "claude-opus-5-5")
    with pytest.raises(CassetteMiss, match="--live"):
        replay.structured(system="s", user="other", schema=MatchDecision)


def test_api_key_alias_and_precedence(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "claude_api_key"):
        monkeypatch.delenv(name, raising=False)
    assert resolve_api_key() is None
    monkeypatch.setenv("claude_api_key", "alias-key")
    assert resolve_api_key() == "alias-key"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "standard-key")
    assert resolve_api_key() == "standard-key"


def test_cassette_key_covers_schema_and_request_parameters(tmp_path):
    from pydantic import BaseModel

    path = tmp_path / "cassette.json"
    inner = CountingProvider()
    CassetteProvider(
        path, inner.model, inner=inner, record=True, params={"effort": "medium"}
    ).structured(system="s", user="u", schema=MatchDecision)

    class Changed(BaseModel):  # same name as far as the old key was concerned
        sku: str | None

    Changed.__name__ = "MatchDecision"
    with pytest.raises(CassetteMiss):
        CassetteProvider(path, inner.model, params={"effort": "medium"}).structured(
            system="s", user="u", schema=Changed
        )
    with pytest.raises(CassetteMiss):
        CassetteProvider(path, inner.model, params={"effort": "low"}).structured(
            system="s", user="u", schema=MatchDecision
        )
    assert not path.with_suffix(".tmp").exists()  # written atomically


def test_cassette_is_safe_to_record_from_many_threads(tmp_path):
    import json
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "cassette.json"
    recorder = CassetteProvider(path, "claude-opus-5-5", inner=CountingProvider(), record=True)
    with ThreadPoolExecutor(8) as pool:
        list(
            pool.map(
                lambda n: recorder.structured(system="s", user=f"line {n}", schema=MatchDecision),
                range(40),
            )
        )
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 40


def test_provider_caps_requests_in_flight():
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    class Slow:
        def __init__(self):
            self.in_flight = self.peak = 0
            self.lock = threading.Lock()

        def parse(self, **kwargs):
            with self.lock:
                self.in_flight += 1
                self.peak = max(self.peak, self.in_flight)
            time.sleep(0.03)
            with self.lock:
                self.in_flight -= 1
            return response()

    slow = Slow()
    client = SimpleNamespace(messages=slow, beta=SimpleNamespace(messages=slow))
    provider = AnthropicProvider(model="claude-haiku-5-5", client=client, concurrency=3)
    with ThreadPoolExecutor(12) as pool:
        list(
            pool.map(
                lambda _: provider.structured(system="s", user="u", schema=MatchDecision), range(12)
            )
        )
    assert 1 < slow.peak <= 3


def test_a_model_without_a_listed_price_is_costed_at_the_highest_price_not_at_zero():
    # A reply served by a model that is not in the price table must still count
    # against a spend cap.
    dearest = max(PRICES.values())
    stats = CallStats("some-model-nobody-priced", 1_000_000, 1_000_000, 0.1)
    assert stats.cost_usd == pytest.approx(dearest[0] + dearest[1])
