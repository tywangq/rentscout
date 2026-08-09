"""FixtureSource: synthetic listing streams from a scenario directory.

Layout:
    scenario.toml   — [scenario] name, start_date; [expected] ids for evals
    day_1.json …    — {"listings": [...]} full snapshot per simulated day
    commutes.json   — {address: minutes} table backing the commute_time tool
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from ..models import Listing


class FixtureSource:
    snapshot = True

    def __init__(self, scenario_dir: str | Path, day: int) -> None:
        self.dir = Path(scenario_dir)
        self.day = day
        meta = tomllib.loads((self.dir / "scenario.toml").read_text())
        self.name = meta["scenario"]["name"]
        self.start_date = meta["scenario"]["start_date"]
        self.expected = meta.get("expected", {})

    def fetch(self) -> list[Listing]:
        data = json.loads((self.dir / f"day_{self.day}.json").read_text())
        return [Listing.from_dict(self.name, d) for d in data["listings"]]

    def commutes(self) -> dict[str, int]:
        path = self.dir / "commutes.json"
        if not path.exists():
            return {}
        return {k: int(v) for k, v in json.loads(path.read_text()).items()}

    @classmethod
    def available_days(cls, scenario_dir: str | Path) -> list[int]:
        days = []
        for path in Path(scenario_dir).glob("day_*.json"):
            match = re.fullmatch(r"day_(\d+)", path.stem)
            if match:
                days.append(int(match.group(1)))
        return sorted(days)
