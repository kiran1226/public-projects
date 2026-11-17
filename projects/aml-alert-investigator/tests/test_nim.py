import json
from types import SimpleNamespace

import pytest

from amlagent.llm import BudgetExceeded, LLMError, StepRequest, Usage
from amlagent.nim import NvidiaProvider, from_chat, to_chat

TOOL = {
    "name": "get_prior_alerts",
    "description": "Earlier alerts.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"customer": {"type": "string"}},
        "required": ["customer"],
        "additionalProperties": False,
    },
}
HISTORY = [
    {"role": "user", "content": "brief"},
    {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "abc"},
            {"type": "text", "text": "Checking history."},
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "get_prior_alerts",
                "input": {"customer": "CUST_01"},
            },
        ],
    },
    {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": '{"prior_alerts": []}'},
            {"type": "text", "text": "One step left."},
        ],
    },
]


def request(**over):
    base = dict(
        model="nvidia/some-model",
        system="You are a reviewer.",
        messages=HISTORY,
        tools=[TOOL],
        max_tokens=4000,
    )
    return StepRequest(**base | over)


def completion(content=None, tool_calls=None, finish="stop"):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30),
    )


def call(name, arguments, id="call_9"):
    return SimpleNamespace(
        id=id, type="function", function=SimpleNamespace(name=name, arguments=arguments)
    )


def test_request_is_translated_to_chat_format():
    sent = to_chat(request())
    assert sent["model"] == "nvidia/some-model" and sent["max_tokens"] == 4000
    assert sent["tool_choice"] == "auto"
    assert sent["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_prior_alerts",
                "description": "Earlier alerts.",
                "parameters": TOOL["input_schema"],
            },
        }
    ]
    assert sent["messages"] == [
        {"role": "system", "content": "You are a reviewer."},
        {"role": "user", "content": "brief"},
        {
            "role": "assistant",
            "content": "Checking history.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "get_prior_alerts",
                        "arguments": '{"customer": "CUST_01"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"prior_alerts": []}'},
        {"role": "user", "content": "One step left."},
    ]


def test_output_schema_is_stated_in_the_prompt():
    schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    }
    sent = to_chat(
        request(tools=[], output_schema=schema, messages=[{"role": "user", "content": "data"}])
    )
    assert "tools" not in sent and "response_format" not in sent
    assert json.dumps(schema) in sent["messages"][0]["content"]


def test_reply_with_tool_calls_is_translated_back():
    reply = from_chat(
        completion(
            content="Looking.",
            finish="tool_calls",
            tool_calls=[call("get_prior_alerts", '{"customer": "CUST_01"}')],
        ),
        "nvidia/some-model",
        1.2,
    )
    assert reply.stop_reason == "tool_use"
    assert reply.content == [
        {"type": "text", "text": "Looking."},
        {
            "type": "tool_use",
            "id": "call_9",
            "name": "get_prior_alerts",
            "input": {"customer": "CUST_01"},
        },
    ]
    assert reply.usage == Usage(input_tokens=120, output_tokens=30) and reply.latency_s == 1.2


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]", "", None])
def test_unusable_tool_arguments_become_an_empty_input(arguments):
    reply = from_chat(
        completion(finish="tool_calls", tool_calls=[call("get_prior_alerts", arguments)]),
        "nvidia/some-model",
        0.1,
    )
    assert reply.content[0]["input"] == {}  # the tool layer then answers with an error result


def test_finish_reasons_map_to_stop_reasons():
    assert from_chat(completion(content="done"), "m/x", 0).stop_reason == "end_turn"
    assert (
        from_chat(completion(content="cut", finish="length"), "m/x", 0).stop_reason == "max_tokens"
    )


def test_provider_calls_the_client_and_caps_requests():
    seen = []

    def create(**kwargs):
        seen.append(kwargs)
        return completion(content="ok")

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    provider = NvidiaProvider(client=client, min_interval_s=0, max_requests=1)
    assert provider.step(request()).content == [{"type": "text", "text": "ok"}]
    assert seen[0]["model"] == "nvidia/some-model"
    with pytest.raises(BudgetExceeded):
        provider.step(request())


def test_endpoint_errors_become_llm_errors():
    def boom(**kwargs):
        raise RuntimeError("503")

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=boom)))
    with pytest.raises(LLMError):
        NvidiaProvider(client=client, min_interval_s=0).step(request())
