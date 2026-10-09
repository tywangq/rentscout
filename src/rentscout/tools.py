"""Tool menu for the investigation loop.

Every tool declares a cost class so budget enforcement is uniform:
  free    — reads local state, unlimited
  metered — external lookups; each call consumes the run's investigation quota
"""

from __future__ import annotations

import json
import statistics
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
        except Exception as exc:  # a tool failure is information for the model, not a crash
            return f"ERROR: {call.name} failed ({type(exc).__name__}); treat it as unknown"


def area_stats(store: Store, listing_id: str) -> dict | str:
    """Area $/sqft context for a listing, or a sentence saying why there is none.

    Prefers RentCast's market statistics for the zip and the listing's bedroom
    count (the whole market, refreshed monthly); falls back to the zip overall,
    then to the median of listings this agent has tracked (pre-filtered to the
    profile's limits, so biased). Used by the compare_to_area tool and in the
    triage payload, so 'good value for the area' is judged against the area.
    """
    row = store.listing(listing_id)
    if row is None:
        return f"unknown listing {listing_id!r}"
    attrs = json.loads(row["attributes"] or "{}")
    zip_code = attrs.get("zip")
    if not zip_code:
        return "no zip code for this listing; no area to compare against"
    own = attrs.get("price_per_sqft")
    beds = int(row["beds"] or 0)
    market = store.market_stats(zip_code)
    stats: dict | None = None
    if market:
        same = (market.get("byBedrooms") or {}).get(str(beds)) or {}
        if same.get("medianRentPerSquareFoot") and (same.get("totalListings") or 0) >= 5:
            stats = {"source": f"RentCast market data, {market['month']}",
                     "scope": f"{same.get('totalListings')} {beds}-bedroom listings",
                     "median_price_per_sqft": same["medianRentPerSquareFoot"],
                     "median_rent_same_beds": same.get("medianRent")}
        elif market.get("medianRentPerSquareFoot"):
            stats = {"source": f"RentCast market data, {market['month']}",
                     "scope": f"{market.get('totalListings')} listings, all sizes",
                     "median_price_per_sqft": market["medianRentPerSquareFoot"]}
    if stats is None:
        peers = store.area_price_per_sqft(zip_code, listing_id)
        if len(peers) < 3:
            return f"only {len(peers)} other tracked listings in {zip_code}; too few to compare"
        stats = {"source": "tracked listings",
                 "scope": f"{len(peers)} others, within this profile's price and "
                          "bedroom limits",
                 "median_price_per_sqft": round(statistics.median(peers), 2)}
    stats["zip"] = zip_code
    if own:
        stats["price_per_sqft"] = own
        stats["vs_median_pct"] = round((own - stats["median_price_per_sqft"])
                                       / stats["median_price_per_sqft"] * 100)
    return stats


def build_registry(
    store: Store, commutes: dict[str, int] | Callable[[str], str]
) -> ToolRegistry:
    """commute_time reads a fixture table offline, or calls live routing
    (ORSCommute) when given a callable; the loop is the same either way."""

    def commute_time(address: str, mode: str | None = None) -> str:
        if callable(commutes):  # live routing (ORSCommute)
            return commutes(address, mode)
        minutes = commutes.get(address)  # fixture table has one mode
        return str(minutes) if minutes is not None else "unknown"

    def compare_to_area(listing_id: str) -> str:
        stats = area_stats(store, listing_id)
        if isinstance(stats, str):
            return stats
        head = (f"{stats['source']} for {stats['zip']} ({stats['scope']}): median "
                f"${stats['median_price_per_sqft']:.2f}/sqft")
        if stats.get("median_rent_same_beds"):
            head += f", median rent ${stats['median_rent_same_beds']:,}"
        own = stats.get("price_per_sqft")
        if own is None:
            return head + "; this listing has no square footage, so it cannot be compared"
        diff = stats["vs_median_pct"]
        side = "below" if diff < 0 else "above"
        return head + f"; this listing ${own:.2f}/sqft, {abs(diff):.0f}% {side} the median"

    def price_history(listing_id: str) -> str:
        history = store.price_history(listing_id)
        if not history:
            return "no history"
        return "; ".join(f"{date}: ${price}" for date, price in history)

    return ToolRegistry(
        [
            Tool(
                name="commute_time",
                description=(
                    "Minutes from this address to the renter's commute anchor. mode: "
                    "bike (default), car (free-flow, no traffic), or walk. There is no "
                    "transit. Each call, each mode, uses quota: pick the modes that fit "
                    "the distance."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "address": {"type": "string"},
                        "mode": {"type": "string", "enum": ["bike", "car", "walk"]},
                    },
                    "required": ["address"],
                },
                cost_class="metered",
                fn=commute_time,
            ),
            Tool(
                name="compare_to_area",
                description=(
                    "This listing's price per sqft against the median of other tracked "
                    "listings in the same zip code. Computed from data already fetched."
                ),
                parameters={
                    "type": "object",
                    "properties": {"listing_id": {"type": "string"}},
                    "required": ["listing_id"],
                },
                cost_class="free",
                fn=compare_to_area,
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
