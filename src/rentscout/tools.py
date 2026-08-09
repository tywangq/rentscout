"""Tool menu for the investigation loop.

Every tool declares a cost class so budget enforcement is uniform:
  free    — reads local state, unlimited
  metered — external lookups; each call consumes the run's investigation quota
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .budget import BudgetGuard
from .llm import ToolCall
from .state import Store

QUOTA_EXHAUSTED = (
    "ERROR: investigation budget exhausted; wrap up with the information you have."
)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    cost_class: str  # "free" | "metered"
    fn: Callable[..., str]


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "name": tool.name,
                "description": f"[{tool.cost_class}] {tool.description}",
                "parameters": tool.parameters,
            }
            for tool in self._tools.values()
        ]

    def execute(self, call: ToolCall, guard: BudgetGuard) -> str:
        tool = self._tools.get(call.name)
        if tool is None:
            return f"ERROR: unknown tool {call.name!r}"
        if tool.cost_class == "metered" and not guard.take_investigation():
            return QUOTA_EXHAUSTED
        try:
            return tool.fn(**call.arguments)
        except TypeError as exc:
            return f"ERROR: bad arguments for {call.name}: {exc}"


def build_registry(store: Store, commutes: dict[str, int]) -> ToolRegistry:
    """M0 tool set. commute_time reads a fixture table; a real transit API
    replaces the lookup in M1 without touching the loop."""

    def commute_time(address: str) -> str:
        minutes = commutes.get(address)
        return str(minutes) if minutes is not None else "unknown"

    def price_history(listing_id: str) -> str:
        history = store.price_history(listing_id)
        if not history:
            return "no history"
        return "; ".join(f"{date}: ${price}" for date, price in history)

    return ToolRegistry(
        [
            Tool(
                name="commute_time",
                description="Minutes from this address to the profile's commute anchor.",
                parameters={
                    "type": "object",
                    "properties": {"address": {"type": "string"}},
                    "required": ["address"],
                },
                cost_class="metered",
                fn=commute_time,
            ),
            Tool(
                name="price_history",
                description="Observed price history for a tracked listing id.",
                parameters={
                    "type": "object",
                    "properties": {"listing_id": {"type": "string"}},
                    "required": ["listing_id"],
                },
                cost_class="free",
                fn=price_history,
            ),
        ]
    )
