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


@dataclass(frozen=True)
class BudgetCaps:
    per_run_dollars: float  # hard LLM spend cap per run
    investigations_per_run: int  # quota of metered (deep) tool calls
    monthly_dollars: float  # ledger cap; runs refuse to start past it
    min_score_to_investigate: int = 6


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
    )
    caps = BudgetCaps(
        per_run_dollars=float(b["per_run_dollars"]),
        investigations_per_run=int(b["investigations_per_run"]),
        monthly_dollars=float(b["monthly_dollars"]),
        min_score_to_investigate=int(b.get("min_score_to_investigate", 6)),
    )
    return profile, caps
