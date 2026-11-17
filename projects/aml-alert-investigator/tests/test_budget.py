import pytest

from amlagent.budget import BudgetedProvider, Ledger
from amlagent.llm import BudgetExceeded, LLMError, ScriptedProvider, StepRequest, Usage
from conftest import reply, text

OPUS_75_CENTS = Usage(input_tokens=100_000, output_tokens=17_500)  # 0.40 + 0.35


def test_ledger_stops_before_the_cap_can_be_passed(tmp_path):
    ledger = Ledger(tmp_path / "spend.jsonl", cap_usd=1.00)
    ledger.check("claude-opus-5-5")
    assert ledger.record("claude-opus-5-5", OPUS_75_CENTS, "AL-001/lead") == pytest.approx(0.75)
    with pytest.raises(BudgetExceeded):
        ledger.check("claude-opus-5-5")  # 0.75 + 0.30 reserve > 1.00
    ledger.check("claude-haiku-5-5")  # 0.75 + 0.02 reserve fits
    assert Ledger(tmp_path / "spend.jsonl", cap_usd=1.00).spent() == pytest.approx(
        0.75
    )  # persisted


def test_run_cap_counts_only_this_run(tmp_path):
    path = tmp_path / "spend.jsonl"
    Ledger(path).record("claude-opus-5-5", OPUS_75_CENTS, "earlier run")
    ledger = Ledger(path, cap_usd=5.00, run_cap_usd=0.25)
    with pytest.raises(BudgetExceeded):
        ledger.check("claude-opus-5-5")  # the 0.30 reserve does not fit in a 0.25 run
    ledger.check("claude-haiku-5-5")


def test_a_damaged_ledger_stops_the_run(tmp_path):
    path = tmp_path / "spend.jsonl"
    path.write_text('{"cost_usd": 0.5}\nnot json\n', encoding="utf-8")
    with pytest.raises(BudgetExceeded, match="damaged"):  # stops the run, like the cap does
        Ledger(path).spent()
    for line in ('{"cost_usd": NaN}', '{"cost_usd": -1}'):
        path.write_text(line + "\n", encoding="utf-8")
        with pytest.raises(BudgetExceeded, match="damaged"):
            Ledger(path).spent()


def test_budgeted_provider_checks_then_records(tmp_path):
    ledger = Ledger(tmp_path / "spend.jsonl", cap_usd=1.00)
    inner = ScriptedProvider(
        [reply(text("ok"), model="claude-opus-5-5", tokens=(100_000, 17_500))] * 2
    )
    provider = BudgetedProvider(inner, ledger)
    step = StepRequest(
        model="claude-opus-5-5", system="s", messages=[{"role": "user", "content": "m"}]
    )
    provider.step(step)
    assert ledger.spent() == pytest.approx(0.75)
    with pytest.raises(BudgetExceeded):
        provider.step(step)
    assert len(inner.requests) == 1  # the second call never left


def test_calls_in_flight_cannot_pass_the_cap_together(tmp_path):
    ledger = Ledger(tmp_path / "spend.jsonl", cap_usd=1.00)
    ledger.record(
        "claude-opus-5-5", Usage(input_tokens=100_000, output_tokens=15_000), "earlier"
    )  # 0.70
    ledger.reserve("claude-opus-5-5")  # 0.70 + 0.30 reserve fits
    with pytest.raises(BudgetExceeded):
        ledger.reserve("claude-opus-5-5")  # a second call in flight would not
    ledger.release("claude-opus-5-5")
    ledger.reserve("claude-opus-5-5")


def test_a_failed_call_releases_its_reservation(tmp_path):
    ledger = Ledger(tmp_path / "spend.jsonl", cap_usd=0.35)
    provider = BudgetedProvider(
        ScriptedProvider(
            [LLMError("503"), reply(text("ok"), model="claude-opus-5-5", tokens=(100, 10))]
        ),
        ledger,
    )
    step = StepRequest(
        model="claude-opus-5-5", system="s", messages=[{"role": "user", "content": "m"}]
    )
    with pytest.raises(LLMError):
        provider.step(step)
    assert provider.step(step).content == [
        text("ok")
    ]  # the reserve of the failed call was given back


def test_the_reserve_grows_with_the_request_so_a_large_call_cannot_pass_the_cap(tmp_path):
    """A fixed reserve assumed a small request. The reserve is now an upper bound on the
    cost of the request that is about to be sent, whatever its size."""
    ledger = Ledger(tmp_path / "spend.jsonl", cap_usd=1.00)
    inner = ScriptedProvider([reply(text("ok"), model="claude-opus-5-5", tokens=(300_000, 100))])
    provider = BudgetedProvider(inner, ledger)
    huge = StepRequest(
        model="claude-opus-5-5",
        system="s",
        messages=[{"role": "user", "content": "row " * 100_000}],  # 400,000 bytes
    )
    with pytest.raises(BudgetExceeded):
        provider.step(huge)  # up to 400,000 input tokens at Opus rates is more than 1.00
    assert inner.requests == [] and ledger.spent() == 0


def test_the_reserve_is_never_below_the_cost_of_the_call(tmp_path):
    from amlagent.budget import reserve_for
    from amlagent.llm import cost_usd

    request = StepRequest(
        model="claude-opus-5-5",
        system="system " * 500,
        messages=[{"role": "user", "content": "brief " * 2_000}],
        max_tokens=6000,
    )
    size = len(request.system) + len(request.messages[0]["content"])
    worst = Usage(input_tokens=0, cache_write_tokens=size, output_tokens=6000)  # all cache writes
    assert reserve_for(request.model, request) >= cost_usd(
        "claude-opus-5", worst
    )  # even a fallback
    small = StepRequest(model="claude-haiku-5-5", system="s", messages=[])
    assert reserve_for(small.model, small) == pytest.approx(0.02)  # the floor still applies
    assert reserve_for("nvidia/some-model", small) == 0.0
