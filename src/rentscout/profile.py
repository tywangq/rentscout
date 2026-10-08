from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SearchProfile:
    name: str
    city: str
    max_price: int
    min_beds: float
    min_baths: float
    neighborhoods: tuple[str, ...]  # empty tuple = anywhere in the city
    excluded_keywords: tuple[str, ...]  # hard dealbreakers, matched in code
    preferences: tuple[str, ...]  # soft, scored by the LLM
    commute_anchor: str
    max_commute_minutes: int
    # Hard limits, enforced in code. The first cold start ranked 170 sq ft rooms
    # in 8-bedroom shared houses at 10/10: cheap enough to win every soft point.
    min_sqft: int | None = None  # listings with unknown size pass; known-small ones do not
    max_beds: float | None = None
    # OpenRouteService profile; it has no transit, so the live default is cycling.
    commute_mode: str = "cycling-regular"


@dataclass(frozen=True)
class BudgetCaps:
    per_run_dollars: float  # hard LLM spend cap per run
    investigations_per_run: int  # quota of metered (deep) tool calls
    monthly_dollars: float  # ledger cap; runs refuse to start past it
    min_score_to_investigate: int = 6
    # External API calls are a budget too: RentCast's free tier is 50/month.
    rentcast_requests_per_month: int = 40
    # Cold start: the first live day had 261 fresh candidates, and triaging them
    # all in one call spent the whole $0.05 run cap before any investigation.
    max_triage_per_run: int = 60  # freshest first; the rest are recorded, not scored
    triage_budget_share: float = 0.6  # triage stops here; the rest is for investigation


def load_profile(path: str | Path) -> tuple[SearchProfile, BudgetCaps]:
    data = tomllib.loads(Path(path).read_text())
    s, b = data["search"], data["budget"]
    profile = SearchProfile(
        name=s["name"],
        city=s["city"],
        max_price=int(s["max_price"]),
        min_beds=float(s["min_beds"]),
        min_baths=float(s["min_baths"]),
        neighborhoods=tuple(s.get("neighborhoods", [])),
        excluded_keywords=tuple(s.get("excluded_keywords", [])),
        preferences=tuple(s.get("preferences", [])),
        commute_anchor=s["commute_anchor"],
        max_commute_minutes=int(s["max_commute_minutes"]),
        commute_mode=s.get("commute_mode", "cycling-regular"),
        min_sqft=int(s["min_sqft"]) if "min_sqft" in s else None,
        max_beds=float(s["max_beds"]) if "max_beds" in s else None,
    )
    caps = BudgetCaps(
        per_run_dollars=float(b["per_run_dollars"]),
        investigations_per_run=int(b["investigations_per_run"]),
        monthly_dollars=float(b["monthly_dollars"]),
        min_score_to_investigate=int(b.get("min_score_to_investigate", 6)),
        rentcast_requests_per_month=int(b.get("rentcast_requests_per_month", 40)),
        max_triage_per_run=int(b.get("max_triage_per_run", 60)),
        triage_budget_share=float(b.get("triage_budget_share", 0.6)),
    )
    return profile, caps
