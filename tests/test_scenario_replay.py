"""M0 acceptance: the full pipeline replays a three-day scenario end-to-end,
offline, and behaves — golds surface, dealbreakers never appear, rejections
stick, changes are tracked, and every run stays inside its budget."""

import json

import pytest

from conftest import PROFILE, SCENARIO
from rentscout.profile import load_profile
from rentscout.sources.fixture import FixtureSource


def decisions_by_action(store, run_id, action):
    return {
        row["listing_id"]: row["detail"]
        for row in store.decisions(run_id)
        if row["action"] == action
    }


def test_three_day_replay(store, run_day):
    _, caps = load_profile(PROFILE)
    expected = FixtureSource(SCENARIO, 1).expected

    # --- day 1 -------------------------------------------------------------
    r1 = run_day(1)
    assert r1.pick_ids[0] == "fixture:fx-101"  # gold on top
    assert r1.pick_ids == (
        "fixture:fx-101", "fixture:fx-107", "fixture:fx-102", "fixture:fx-106",
    )
    for bad in expected["hard_filtered_ids"]:
        assert bad not in r1.pick_ids
    filtered = decisions_by_action(store, r1.run_id, "hard_filtered")
    assert set(filtered) == {"fixture:fx-103", "fixture:fx-104", "fixture:fx-105"}

    # injection listing is scored honestly (7, not the demanded 10/10),
    # and its over-limit commute is called out
    triaged = decisions_by_action(store, r1.run_id, "triaged")
    assert json.loads(triaged["fixture:fx-106"])["score"] == 7
    assert "OVER the 35 min max" in r1.digest

    assert 0 < r1.spent <= caps.per_run_dollars
    assert not r1.halted
    assert r1.digest_path.exists()
    assert "Investigations: 4 of 10" in r1.digest

    # the user rejects the sketchy listing
    store.add_feedback("fixture:fx-106", "down", "sketchy description")

    # --- day 2 -------------------------------------------------------------
    r2 = run_day(2)
    assert r2.pick_ids == ("fixture:fx-201", "fixture:fx-202")
    assert "fixture:fx-101" not in r2.pick_ids  # no duplicate alert
    assert "fixture:fx-107" not in r2.pick_ids
    assert "Price drop: 416 E Olive Way, Seattle, WA — $1950 → $1875" in r2.digest
    assert "Delisted: 5017 Ballard Ave NW, Seattle, WA" in r2.digest
    assert "Delisted: 1111 E John St, Seattle, WA" in r2.digest

    # --- day 3 -------------------------------------------------------------
    r3 = run_day(3)
    assert r3.pick_ids == ("fixture:fx-301",)
    # the rejected listing re-lists: tracked as a change, never re-alerted
    assert "Re-listed: 1111 E John St, Seattle, WA" in r3.digest
    assert "fixture:fx-106" not in r3.pick_ids
    assert "fixture:fx-106" in decisions_by_action(
        store, r3.run_id, "suppressed_rejected"
    )
    assert "1 suppressed (previously rejected by you)" in r3.digest

    # --- ledger ------------------------------------------------------------
    total = r1.spent + r2.spent + r3.spent
    assert store.month_spend("2026-08") == pytest.approx(total)
    assert total < caps.monthly_dollars
