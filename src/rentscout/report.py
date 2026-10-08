"""Run report: one row per run, built only from the state database.

This is the monitoring view. Every number comes from the trace the pipeline
already writes (runs, decisions, tool logs, spend, API usage), so the report
cannot disagree with what the agent actually did.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from .evals.grounding import check_state
from .profile import SearchProfile


@dataclass
class RunRow:
    run_date: str
    status: str
    fresh: int
    triaged: int
    investigated: int
    tool_calls: int
    commute_unknown: int
    commute_calls: int
    spent: float
    unsourced_notes: int
    hedged_notes: int


def build_report(db_path: str, profile: SearchProfile) -> tuple[list[RunRow], dict[str, int]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    grounding = check_state(
        db_path, max_price=profile.max_price,
        max_commute=profile.max_commute_minutes, preferences=profile.preferences,
    )
    hard_by_run: dict[str, int] = {}
    soft_by_run: dict[str, int] = {}
    for v in grounding.verdicts:
        hard_by_run[v.run_id] = hard_by_run.get(v.run_id, 0) + bool(v.hard)
        soft_by_run[v.run_id] = soft_by_run.get(v.run_id, 0) + bool(v.soft)

    rows = []
    for run in conn.execute("SELECT * FROM runs ORDER BY started_at"):
        decisions = conn.execute(
            "SELECT listing_id, action, detail FROM decisions WHERE run_id = ?",
            (run["run_id"],),
        ).fetchall()
        actions = [d["action"] for d in decisions]
        tool_calls = commute_calls = commute_unknown = 0
        for d in decisions:
            if d["action"] != "investigated":
                continue
            detail = json.loads(d["detail"])
            tool_calls += detail.get("tool_calls", 0)
            for call in detail.get("tool_log", []):
                if call.get("name") == "commute_time":
                    commute_calls += 1
                    result = str(call.get("result", ""))
                    commute_unknown += not result[:1].isdigit()
        listings_seen = {d["listing_id"] for d in decisions if d["listing_id"]}
        rows.append(RunRow(
            run_date=run["run_date"],
            status=run["status"],
            fresh=len(listings_seen),
            triaged=actions.count("triaged"),
            investigated=actions.count("investigated"),
            tool_calls=tool_calls,
            commute_unknown=commute_unknown,
            commute_calls=commute_calls,
            spent=run["dollars"] or 0.0,
            unsourced_notes=hard_by_run.get(run["run_id"], 0),
            hedged_notes=soft_by_run.get(run["run_id"], 0),
        ))
    usage = {
        f"{r['api']} {r['month']}": r["calls"]
        for r in conn.execute("SELECT * FROM api_usage ORDER BY month")
    } if conn.execute("SELECT 1 FROM sqlite_master WHERE name='api_usage'").fetchone() else {}
    conn.close()
    return rows, usage


def render_markdown(rows: list[RunRow], usage: dict[str, int]) -> str:
    lines = [
        "| Date | Status | Listings | Triaged | Investigated | Tool calls "
        "| Commute unknown | Spend | Unsourced notes | Hedged notes |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        status = r.status if len(r.status) <= 24 else r.status[:21] + "..."
        unknown = f"{r.commute_unknown}/{r.commute_calls}" if r.commute_calls else "-"
        lines.append(
            f"| {r.run_date} | {status} | {r.fresh} | {r.triaged} | {r.investigated} "
            f"| {r.tool_calls} | {unknown} | ${r.spent:.4f} | {r.unsourced_notes} "
            f"| {r.hedged_notes} |"
        )
    total = sum(r.spent for r in rows)
    lines.append("")
    lines.append(f"Total spend: ${total:.4f} over {len(rows)} runs.")
    for key, calls in usage.items():
        lines.append(f"API usage, {key}: {calls} requests.")
    return "\n".join(lines) + "\n"
