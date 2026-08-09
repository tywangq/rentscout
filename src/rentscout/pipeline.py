"""One daily run.

Deterministic code decides what enters and leaves the pipeline (fetch, dedupe,
hard constraints, rejection memory, budgets); the LLM decides ranking and where
to spend its investigation quota. Autonomy only where it pays.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from .budget import BudgetExceeded, BudgetGuard, MonthlyCapReached
from .digest import DigestData, Pick, render_digest
from .filters import hard_filter
from .investigate import investigate
from .llm import LLMClient
from .profile import BudgetCaps, SearchProfile
from .sources.base import ListingSource
from .state import Store
from .tools import ToolRegistry
from .triage import FALLBACK_SCORE, TriageScore, triage


@dataclass(frozen=True)
class RunResult:
    run_id: str
    digest_path: Path
    digest: str
    spent: float
    halted: bool
    new_count: int
    pick_ids: tuple[str, ...]


def daily_run(
    *,
    source: ListingSource,
    store: Store,
    profile: SearchProfile,
    caps: BudgetCaps,
    llm: LLMClient,
    registry: ToolRegistry,
    run_date: str,
    out_dir: str | Path,
) -> RunResult:
    run_id = f"{run_date}-{uuid.uuid4().hex[:8]}"
    try:
        guard = BudgetGuard(caps, store.month_spend(run_date[:7]))
    except MonthlyCapReached as exc:
        store.record_refused_run(run_id, run_date, str(exc))
        raise
    store.start_run(run_id, run_date)

    reconciled = store.reconcile(run_date, source.fetch(), snapshot=source.snapshot)

    passed, filtered = hard_filter(profile, reconciled.fresh)
    for listing, reason in filtered:
        store.record_decision(run_id, listing.id, "hard_filtered", reason)

    rejected = store.rejected_ids()
    suppressed = [l for l in passed if l.id in rejected]
    for listing in suppressed:
        store.record_decision(run_id, listing.id, "suppressed_rejected")
    candidates = [l for l in passed if l.id not in rejected]

    halted = False
    scores: dict[str, TriageScore] = {}
    if candidates:
        try:
            scores = {
                ts.listing_id: ts
                for ts in triage(llm, guard, profile, candidates)
            }
            for ts in scores.values():
                store.record_decision(
                    run_id, ts.listing_id, "triaged",
                    {"score": ts.score, "reason": ts.reason},
                )
        except BudgetExceeded as exc:
            halted = True
            scores = {
                l.id: TriageScore(l.id, FALLBACK_SCORE, "not triaged (budget)")
                for l in candidates
            }
            store.record_decision(run_id, None, "halted_before_triage", str(exc))

    ranked = sorted(candidates, key=lambda l: (-scores[l.id].score, l.price))
    notes: dict[str, str] = {}
    skipped_low = skipped_budget = 0
    for listing in ranked:
        ts = scores[listing.id]
        if ts.score < caps.min_score_to_investigate:
            skipped_low += 1
            store.record_decision(
                run_id, listing.id, "skipped_low_score", {"score": ts.score}
            )
            continue
        if halted or guard.llm_exhausted:
            skipped_budget += 1
            store.record_decision(run_id, listing.id, "skipped_budget")
            continue
        try:
            note = investigate(llm, guard, profile, listing, ts, registry)
            notes[listing.id] = note.note
            store.record_decision(
                run_id, listing.id, "investigated",
                {"note": note.note, "tool_calls": note.tool_calls_made},
            )
        except BudgetExceeded as exc:
            halted = True
            skipped_budget += 1
            store.record_decision(run_id, listing.id, "skipped_budget", str(exc))

    # Any budget-driven skip means the digest is truncated; say so honestly.
    halted = halted or skipped_budget > 0

    picks = [
        Pick(
            listing=l,
            score=scores[l.id].score,
            reason=scores[l.id].reason,
            note=notes.get(l.id),
        )
        for l in ranked
        if scores[l.id].score >= caps.min_score_to_investigate
    ]
    digest = render_digest(
        DigestData(
            run_date=run_date,
            profile_name=profile.name,
            picks=picks,
            price_changes=[(l, old) for l, old in reconciled.price_changed],
            delisted=reconciled.delisted,
            relisted=reconciled.relisted,
            filtered=filtered,
            suppressed_count=len(suppressed),
            skipped_low_score=skipped_low,
            skipped_budget=skipped_budget,
            spent=guard.spent,
            cap=caps.per_run_dollars,
            investigations_used=guard.investigations_used,
            quota=caps.investigations_per_run,
            halted=halted,
        )
    )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    digest_path = out_dir / f"digest_{run_date}.md"
    digest_path.write_text(digest)

    store.record_spend(run_id, run_date, "llm", guard.spent, detail=llm.model)
    store.finish_run(
        run_id, "halted" if halted else "ok", guard.spent, str(digest_path)
    )
    return RunResult(
        run_id=run_id,
        digest_path=digest_path,
        digest=digest,
        spent=guard.spent,
        halted=halted,
        new_count=len(reconciled.new),
        pick_ids=tuple(p.listing.id for p in picks),
    )
