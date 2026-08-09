"""All budget enforcement lives here, in code — never in a prompt.

The per-run dollar cap is stop-once-crossed: a call's true cost is only known
after it returns, so overshoot is bounded by a single call. Callers must invoke
require_llm_budget() before every LLM call and charge_llm() after it.
"""

from __future__ import annotations

from .profile import BudgetCaps


class BudgetExceeded(RuntimeError):
    """Per-run LLM dollar cap crossed; no further LLM calls this run."""


class MonthlyCapReached(RuntimeError):
    """Monthly ledger cap reached; the run refuses to start."""


class BudgetGuard:
    def __init__(self, caps: BudgetCaps, month_spent: float) -> None:
        if month_spent >= caps.monthly_dollars:
            raise MonthlyCapReached(
                f"monthly ledger at ${month_spent:.4f} of "
                f"${caps.monthly_dollars:.2f} cap"
            )
        self.caps = caps
        self._spent = 0.0
        self._investigations = 0

    def require_llm_budget(self) -> None:
        if self.llm_exhausted:
            raise BudgetExceeded(
                f"run spend ${self._spent:.6f} reached "
                f"${self.caps.per_run_dollars:.4f} cap"
            )

    def charge_llm(self, dollars: float) -> None:
        self._spent += dollars

    @property
    def llm_exhausted(self) -> bool:
        return self._spent >= self.caps.per_run_dollars

    def take_investigation(self) -> bool:
        """Consume one unit of the metered-tool quota; False when exhausted."""
        if self._investigations >= self.caps.investigations_per_run:
            return False
        self._investigations += 1
        return True

    @property
    def spent(self) -> float:
        return self._spent

    @property
    def investigations_used(self) -> int:
        return self._investigations
