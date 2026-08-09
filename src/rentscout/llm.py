"""LLM client interface plus offline stand-ins.

Prompts embed their machine-readable payload in a fenced ```json block; the
real model reads it as context, and the offline stand-ins parse it. That keeps
the message flow identical whether or not a real API is on the other end.

The real OpenAI client lands in M1 behind this same protocol.
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


@dataclass(frozen=True)
class LLMReply:
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = Usage(0, 0)


class LLMClient(Protocol):
    model: str

    def complete(
        self, messages: list[dict], tools: list[dict] | None = None
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
        self, messages: list[dict], tools: list[dict] | None = None
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
        self, messages: list[dict], tools: list[dict] | None = None
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
        scores = []
        for listing in payload["listings"]:
            score, matched = self._score(profile, listing)
            reason = (
                "matches: " + ", ".join(matched) if matched else "no preference hits"
            )
            scores.append(
                {"id": listing["id"], "score": score, "reason": reason}
            )
        text = json.dumps(scores)
        return LLMReply(text=text, usage=_estimate_usage(messages, text))

    @staticmethod
    def _score(profile: dict, listing: dict) -> tuple[int, list[str]]:
        score = 5
        if listing["price"] <= 0.9 * profile["max_price"]:
            score += 2
        elif listing["price"] <= profile["max_price"]:
            score += 1
        description = listing.get("description", "").lower()
        matched = [p for p in profile["preferences"] if p.lower() in description]
        score += min(3, len(matched))
        if listing.get("neighborhood") in profile["neighborhoods"]:
            score += 1
        return max(0, min(10, score)), matched

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
