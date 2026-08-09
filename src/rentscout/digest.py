"""Digest rendering: deterministic Markdown from structured run data.

The digest must be valid even when a run halts early — a truncated report with
an honest budget line beats a crashed run.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Listing


@dataclass(frozen=True)
class Pick:
    listing: Listing
    score: int
    reason: str
    note: str | None  # None = passed triage but was never investigated


@dataclass
class DigestData:
    run_date: str
    profile_name: str
    picks: list[Pick] = field(default_factory=list)
    price_changes: list[tuple[Listing, int]] = field(default_factory=list)  # (listing, old_price)
    delisted: list[Listing] = field(default_factory=list)
    relisted: list[Listing] = field(default_factory=list)
    filtered: list[tuple[Listing, str]] = field(default_factory=list)
    suppressed_count: int = 0
    skipped_low_score: int = 0
    skipped_budget: int = 0
    spent: float = 0.0
    cap: float = 0.0
    investigations_used: int = 0
    quota: int = 0
    halted: bool = False


def render_digest(d: DigestData) -> str:
    lines = [f"# RentScout digest — {d.run_date} (profile: {d.profile_name})", ""]

    lines.append(f"## Top picks ({len(d.picks)})")
    if not d.picks:
        lines.append("Nothing new worth a look today.")
    for i, pick in enumerate(d.picks, 1):
        l = pick.listing
        lines.append(
            f"{i}. **${l.price}/mo · {l.beds:g}bd/{l.baths:g}ba · "
            f"{l.neighborhood}** — {l.address}"
        )
        lines.append(f"   Score {pick.score}/10 — {pick.reason}")
        if pick.note:
            lines.append(f"   Note: {pick.note}")
        else:
            lines.append("   Note: not investigated this run.")
        if l.url:
            lines.append(f"   {l.url}")
    lines.append("")

    changes = []
    for listing, old_price in d.price_changes:
        direction = "drop" if listing.price < old_price else "increase"
        changes.append(
            f"- Price {direction}: {listing.address} — "
            f"${old_price} → ${listing.price}"
        )
    changes.extend(
        f"- Delisted: {l.address} (${l.price}/mo)" for l in d.delisted
    )
    changes.extend(
        f"- Re-listed: {l.address} (${l.price}/mo)" for l in d.relisted
    )
    lines.append("## Changes on tracked listings")
    lines.extend(changes if changes else ["No changes."])
    lines.append("")

    lines.append("## Not shown")
    not_shown = []
    for listing, reason in d.filtered:
        not_shown.append(f"- Filtered ({reason}): {listing.address}")
    if d.suppressed_count:
        not_shown.append(
            f"- {d.suppressed_count} suppressed (previously rejected by you)"
        )
    if d.skipped_low_score:
        not_shown.append(
            f"- {d.skipped_low_score} skipped investigation (score below threshold)"
        )
    if d.skipped_budget:
        not_shown.append(
            f"- {d.skipped_budget} skipped investigation (budget exhausted)"
        )
    lines.extend(not_shown if not_shown else ["Nothing withheld."])
    lines.append("")

    lines.append("## Run economics")
    lines.append(f"- LLM spend: ${d.spent:.6f} of ${d.cap:.4f} cap")
    lines.append(f"- Investigations: {d.investigations_used} of {d.quota}")
    status = "HALTED EARLY — LLM budget cap reached" if d.halted else "completed"
    lines.append(f"- Status: {status}")
    lines.append("")
    return "\n".join(lines)
