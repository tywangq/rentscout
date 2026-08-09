from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from pathlib import Path

import pytest

from rentscout.llm import RuleBasedLLM
from rentscout.pipeline import daily_run
from rentscout.profile import load_profile
from rentscout.sources.fixture import FixtureSource
from rentscout.state import Store
from rentscout.tools import build_registry

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "fixtures" / "scenarios" / "basic"
PROFILE = ROOT / "examples" / "profile.toml"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "state.db")
    yield s
    s.close()


@pytest.fixture
def profile_caps():
    return load_profile(PROFILE)


@pytest.fixture
def run_day(store, tmp_path):
    """Run one simulated scenario day through the full pipeline, offline."""

    def _run(day: int, **caps_overrides):
        profile, caps = load_profile(PROFILE)
        if caps_overrides:
            caps = dataclasses.replace(caps, **caps_overrides)
        source = FixtureSource(SCENARIO, day)
        run_date = (
            date.fromisoformat(source.start_date) + timedelta(days=day - 1)
        ).isoformat()
        return daily_run(
            source=source,
            store=store,
            profile=profile,
            caps=caps,
            llm=RuleBasedLLM(),
            registry=build_registry(store, source.commutes()),
            run_date=run_date,
            out_dir=tmp_path / "runs",
        )

    return _run
