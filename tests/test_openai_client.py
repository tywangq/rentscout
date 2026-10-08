"""OpenAIClient against a stubbed SDK: no network, no API key."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

from rentscout.llm import OpenAIClient, ToolCall, to_responses_input
from rentscout.triage import _strip_fence


class FakeResponses:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self._responses.pop(0)


def _resp(output=(), text="", tokens=(100, 20)):
    return NS(
        output=list(output),
        output_text=text,
        usage=NS(input_tokens=tokens[0], output_tokens=tokens[1]),
    )


def _client(*responses) -> tuple[OpenAIClient, FakeResponses]:
    fake = FakeResponses(list(responses))
    return OpenAIClient(model="gpt-4.1-mini", client=NS(responses=fake)), fake


def test_function_call_items_become_tool_calls_with_real_usage():
    call = NS(
        type="function_call",
        name="commute_time",
        arguments='{"address": "1 Pine St"}',
        call_id="call_1",
    )
    llm, fake = _client(_resp(output=[call], tokens=(321, 12)))

    reply = llm.complete([{"role": "user", "content": "hi"}], tools=[{"name": "x"}])

    assert reply.tool_calls == (
        ToolCall("commute_time", {"address": "1 Pine St"}, id="call_1"),
    )
    assert (reply.usage.input_tokens, reply.usage.output_tokens) == (321, 12)
    assert fake.requests[0]["tools"] == [{"name": "x"}]


def test_no_tools_key_when_none_offered():
    llm, fake = _client(_resp(text="[]"))
    llm.complete([{"role": "user", "content": "score"}])
    assert "tools" not in fake.requests[0]


def test_malformed_arguments_degrade_to_empty_dict():
    call = NS(type="function_call", name="t", arguments="{not json", call_id="c")
    llm, _ = _client(_resp(output=[call]))
    assert llm.complete([{"role": "user", "content": "x"}]).tool_calls[0].arguments == {}


def test_tool_round_trip_keeps_call_ids_paired():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "[]",
            "tool_calls": [{"id": "call_1", "name": "commute_time", "arguments": {"a": 1}}],
        },
        {"role": "tool", "name": "commute_time", "tool_call_id": "call_1", "content": "18"},
    ]
    items = to_responses_input(messages)
    assert items[2] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "commute_time",
        "arguments": json.dumps({"a": 1}),
    }
    assert items[3] == {"type": "function_call_output", "call_id": "call_1", "output": "18"}


def test_triage_tolerates_fenced_json():
    assert json.loads(_strip_fence('```json\n[{"id": "a"}]\n```')) == [{"id": "a"}]
    assert _strip_fence('[1]') == '[1]'
