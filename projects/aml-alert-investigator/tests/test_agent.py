import pytest

from amlagent.agent import RoleConfig, run_loop
from amlagent.llm import BudgetExceeded, CassetteMiss, LeakBlocked, LLMError, ScriptedProvider
from conftest import reply, text, tool_use

ROLE = RoleConfig(
    name="triage",
    model="claude-haiku-5-5",
    effort="low",
    system="sys",
    tools=[{"name": "look"}, {"name": "submit"}],
    submit_tool="submit",
    max_steps=4,
    max_tokens=1000,
)
DONE = tool_use("submit", {"answer": 1}, id="tu_9")


def run(script, execute=lambda name, tool_input: ('{"ok": true}', False), parse=dict, **kw):
    provider = ScriptedProvider(script)
    return run_loop(provider, ROLE, "brief", execute, parse, tag="AL-1/triage", **kw), provider


def test_tool_call_then_submit():
    result, provider = run([reply(tool_use("look", {"q": 1})), reply(DONE)])
    assert result.submission == {"answer": 1} and result.failure is None
    assert [s.role for s in result.steps] == ["triage", "triage"]
    assert result.steps[0].tool_calls[0].output == '{"ok": true}'
    assert result.steps[0].cost_usd == pytest.approx((1000 * 0.10 + 200 * 0.50) / 1e6)
    second = provider.requests[1]
    assert second.tag == "AL-1/triage" and second.system == "sys"
    assert second.messages[1] == {"role": "assistant", "content": [tool_use("look", {"q": 1})]}
    assert second.messages[2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "tu_1",
        "content": '{"ok": true}',
    }


def test_parallel_calls_come_back_in_one_message_in_order():
    calls = [tool_use("look", {"q": "a"}, id="a"), tool_use("look", {"q": "b"}, id="b")]
    result, provider = run([reply(*calls), reply(DONE)], execute=lambda n, i: (i["q"], False))
    results = [b for b in provider.requests[1].messages[2]["content"] if b["type"] == "tool_result"]
    assert [(r["tool_use_id"], r["content"]) for r in results] == [("a", "a"), ("b", "b")]
    assert len(result.steps[0].tool_calls) == 2


def test_tool_errors_are_marked():
    result, provider = run(
        [reply(tool_use("look", {})), reply(DONE)], execute=lambda n, i: ("bad token", True)
    )
    block = provider.requests[1].messages[2]["content"][0]
    assert block["is_error"] is True and result.steps[0].tool_calls[0].is_error


def test_missing_submit_is_reprompted_once_then_fails():
    result, provider = run([reply(text("I think it is fine."), stop="end_turn")] * 2)
    assert result.failure == "model_failure: no submit call" and result.submission is None
    assert "submit" in provider.requests[1].messages[-1]["content"]


def test_invalid_submission_gets_one_retry():
    def parse(data):
        if data["answer"] == 1:
            raise ValueError("a close needs a reason_code")
        return data

    good = tool_use("submit", {"answer": 2}, id="tu_10")
    result, provider = run([reply(DONE), reply(good)], parse=parse)
    assert result.submission == {"answer": 2}
    retry = provider.requests[1].messages[2]["content"][0]
    assert retry["is_error"] is True and "reason_code" in retry["content"]

    result, _ = run([reply(DONE), reply(DONE)], parse=parse)
    assert result.failure == "model_failure: invalid submission"


def test_step_limit_warns_then_stops():
    look = reply(tool_use("look", {}))
    result, provider = run([look] * 4)
    assert result.failure == "limit" and len(result.steps) == 4
    warned = provider.requests[3].messages[-1]["content"][-1]
    assert warned["type"] == "text" and "one step left" in warned["text"].lower()


@pytest.mark.parametrize(
    "response, failure",
    [
        (reply(text("no"), stop="refusal"), "model_failure: refusal"),
        (reply(text("cut"), stop="max_tokens"), "model_failure: max_tokens"),
        (LLMError("503"), "model_failure: 503"),
        (LeakBlocked(["iban_pattern"]), "leak_blocked"),
    ],
)
def test_failures_end_the_loop_without_a_submission(response, failure):
    result, _ = run([response])
    assert result.failure == failure and result.submission is None


@pytest.mark.parametrize("error", [CassetteMiss("no recording"), BudgetExceeded("cap")])
def test_run_level_errors_propagate(error):
    with pytest.raises(type(error)):
        run([error])


def test_on_step_sees_each_step_as_it_happens():
    seen = []
    run([reply(tool_use("look", {})), reply(DONE)], on_step=seen.append)
    assert len(seen) == 2


def test_case_cost_limit_stops_the_loop():
    result, _ = run([reply(tool_use("look", {})), reply(DONE)], cost_left=lambda: -0.01)
    assert result.failure == "limit" and len(result.steps) == 1
