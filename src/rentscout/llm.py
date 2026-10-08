"""LLM client interface plus offline stand-ins.

Prompts embed their machine-readable payload in a fenced ```json block; the
real model reads it as context, and the offline stand-ins parse it. That keeps
the message flow identical whether or not a real API is on the other end.

OpenAIClient is the real model behind this same protocol (Responses API).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict
    id: str = ""


@dataclass(frozen=True)
class LLMReply:
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = Usage(0, 0)


class LLMClient(Protocol):
    model: str

    def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        schema: dict | None = None,
    ) -> LLMReply: ...


_JSON_BLOCK = re.compile(r"```json\n(.*?)\n```", re.DOTALL)


def extract_payload(messages: list[dict]) -> dict:
    for msg in reversed(messages):
        if msg["role"] == "user":
            match = _JSON_BLOCK.search(msg["content"])
            if match:
                return json.loads(match.group(1))
    raise ValueError("no ```json payload block found in user messages")


def _estimate_usage(messages: list[dict], output_text: str) -> Usage:
    input_chars = sum(len(m.get("content") or "") for m in messages)
    return Usage(max(1, input_chars // 4), max(1, len(output_text) // 4))


class ScriptedLLM:
    """Plays back a fixed list of replies; for tests that need exact control."""

    model = "scripted-fake"

    def __init__(self, replies: list[LLMReply]) -> None:
        self._replies = list(replies)

    def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        schema: dict | None = None,
    ) -> LLMReply:
        if not self._replies:
            raise AssertionError("ScriptedLLM: script exhausted")
        return self._replies.pop(0)


class RuleBasedLLM:
    """Deterministic stand-in used for M0, CI, and demo mode. Not the product.

    Scores with transparent rules so the full pipeline — prompts, tool loop,
    budget accounting — runs end-to-end with zero API calls. It reads only the
    structured payload, never freeform instructions, so it is trivially immune
    to injection; the real injection evals run against live models in M3.
    """

    model = "rule-based-fake"

    def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        schema: dict | None = None,
    ) -> LLMReply:
        payload = extract_payload(messages)
        task = payload["task"]
        if task == "triage":
            return self._triage(messages, payload)
        if task == "investigate":
            return self._investigate(messages, payload)
        raise ValueError(f"RuleBasedLLM: unknown task {task!r}")

    # -- triage ------------------------------------------------------------

    def _triage(self, messages: list[dict], payload: dict) -> LLMReply:
        profile = payload["profile"]
        entries = []
        for listing in payload["listings"]:
            description = listing.get("description", "").lower()
            verdicts = {
                p: "yes" if p.lower() in description else "unknown"
                for p in profile["preferences"]
            }
            matched = [p for p, v in verdicts.items() if v == "yes"]
            reason = (
                "matches: " + ", ".join(matched) if matched else "no preference hits"
            )
            entries.append({"id": listing["id"], "verdicts": verdicts, "reason": reason})
        text = json.dumps({"listings": entries})
        return LLMReply(text=text, usage=_estimate_usage(messages, text))

    # -- investigation -----------------------------------------------------

    def _investigate(self, messages: list[dict], payload: dict) -> LLMReply:
        tool_results = [m for m in messages if m["role"] == "tool"]
        if not tool_results:
            call = ToolCall(
                name="commute_time",
                arguments={"address": payload["listing"]["address"]},
            )
            return LLMReply(
                text="",
                tool_calls=(call,),
                usage=_estimate_usage(messages, call.name),
            )
        text = self._note(payload, tool_results[-1]["content"])
        return LLMReply(text=text, usage=_estimate_usage(messages, text))

    @staticmethod
    def _note(payload: dict, commute_result: str) -> str:
        profile, listing = payload["profile"], payload["listing"]
        max_minutes = profile["max_commute_minutes"]
        try:
            minutes = int(commute_result)
            verdict = "within" if minutes <= max_minutes else "OVER"
            commute = f"Commute {minutes} min ({verdict} the {max_minutes} min max)."
        except ValueError:
            commute = f"Commute unknown ({commute_result})."
        return (
            f"{commute} ${listing['price']}/mo vs ${profile['max_price']} budget. "
            f"Triage: {payload['triage']['reason']}."
        )


class OpenAIClient:
    """Real model via the OpenAI Responses API.

    Translates the loop's provider-neutral messages into Responses input items:
    an assistant turn that made tool calls becomes function_call items, and a
    tool result becomes a function_call_output tied to the same call_id.
    """

    def __init__(self, model: str = "gpt-4.1-mini", client=None) -> None:
        if client is None:
            from openai import OpenAI  # optional dependency: rentscout[openai]

            client = OpenAI()
        self.model = model
        self._client = client

    def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        schema: dict | None = None,
    ) -> LLMReply:
        kwargs = {"model": self.model, "input": to_responses_input(messages)}
        if tools:
            kwargs["tools"] = tools
        if schema:  # structured output: the API guarantees a reply matching it
            kwargs["text"] = {
                "format": {"type": "json_schema", "name": "reply",
                           "schema": schema, "strict": True}
            }
        resp = self._client.responses.create(**kwargs)
        calls = tuple(
            ToolCall(
                name=item.name,
                arguments=_parse_arguments(item.arguments),
                id=item.call_id,
            )
            for item in resp.output
            if item.type == "function_call"
        )
        usage = Usage(resp.usage.input_tokens, resp.usage.output_tokens)
        return LLMReply(text=resp.output_text or "", tool_calls=calls, usage=usage)


def to_responses_input(messages: list[dict]) -> list[dict]:
    items: list[dict] = []
    for msg in messages:
        role = msg["role"]
        if role == "assistant" and msg.get("tool_calls"):
            for call in msg["tool_calls"]:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call["id"],
                        "name": call["name"],
                        "arguments": json.dumps(call["arguments"]),
                    }
                )
        elif role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": msg["tool_call_id"],
                    "output": msg["content"],
                }
            )
        else:
            items.append({"role": role, "content": msg["content"]})
    return items


def _parse_arguments(raw: str) -> dict:
    """A model can emit malformed JSON; hand the tool an empty dict and let
    the registry report bad arguments back to the model instead of crashing."""
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
