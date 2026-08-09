import pytest

from rentscout.budget import BudgetExceeded, BudgetGuard, MonthlyCapReached
from rentscout.profile import BudgetCaps

CAPS = BudgetCaps(
    per_run_dollars=0.05, investigations_per_run=2, monthly_dollars=2.0
)


def test_monthly_cap_refuses_start():
    with pytest.raises(MonthlyCapReached):
        BudgetGuard(CAPS, month_spent=2.0)


def test_dollar_cap_stops_once_crossed():
    guard = BudgetGuard(CAPS, month_spent=0.0)
    guard.require_llm_budget()  # fresh guard passes
    guard.charge_llm(0.05)  # charging never raises (cost known post-call)
    assert guard.llm_exhausted
    with pytest.raises(BudgetExceeded):
        guard.require_llm_budget()


def test_investigation_quota():
    guard = BudgetGuard(CAPS, month_spent=0.0)
    assert guard.take_investigation()
    assert guard.take_investigation()
    assert not guard.take_investigation()
    assert guard.investigations_used == 2


def test_zero_cap_run_still_writes_valid_digest(store, run_day):
    result = run_day(1, per_run_dollars=0.0)
    assert result.halted
    assert result.spent == 0.0  # zero cap means zero LLM calls
    assert result.digest_path.exists()
    assert "HALTED EARLY" in result.digest
    assert store.run(result.run_id)["status"] == "halted"


def test_quota_exhaustion_degrades_gracefully(store, run_day):
    # Day 1 has 4 investigable candidates but only 2 metered calls allowed.
    result = run_day(1, investigations_per_run=2)
    assert not result.halted  # quota is rationing, not a halt
    assert "Investigations: 2 of 2" in result.digest
    assert "Commute unknown" in result.digest  # later candidates hit the quota wall
    assert store.run(result.run_id)["status"] == "ok"


def test_monthly_cap_refuses_pipeline_run(store, run_day):
    store.record_spend("prior", "2026-08-01", "llm", 2.0, "earlier runs")
    with pytest.raises(MonthlyCapReached):
        run_day(1)
    statuses = [row["status"] for row in store.all_runs()]
    assert any(s.startswith("refused:") for s in statuses)
