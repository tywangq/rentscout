"""Groundedness of investigation notes, checked deterministically from the trace.

A note may only state numbers it was given: the listing's own fields, the
profile's limits, or what a tool returned during that investigation. The first
live run showed why this matters: with routing down, the model wrote "Capitol
Hill is generally within the 35-minute range" and "2 beds likely implies 600
sqft" -- fluent, plausible, and unsupported.

Checks (hard = a number with no source; soft = hedged guessing):
  invented_commute   a commute in minutes no tool returned
  invented_sqft      a square footage that isn't the listing's
  invented_price     a dollar figure that isn't price, $/sqft, budget or history
  speculation        hedging words that usually mark a guess ("generally", ...)
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

MINUTES = re.compile(r"(\d+)\s*(?:-|–)?\s*min(?:ute)?s?\b", re.I)
SQFT = re.compile(r"(\d[\d,]*)\s*(?:sq\.?\s*ft|sqft|square[\s-]+f(?:ee|oo)t)", re.I)
DOLLARS = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")
HEDGES = (
    "generally", "typically", "usually", "likely", "probably", "presumably",
    "should be manageable", "tends to", "it is expected", "i assume",
)


@dataclass
class NoteVerdict:
    run_id: str
    listing_id: str
    hard: list[str] = field(default_factory=list)
    soft: list[str] = field(default_factory=list)


@dataclass
class GroundingReport:
    verdicts: list[NoteVerdict]

    @property
    def notes(self) -> int:
        return len(self.verdicts)

    @property
    def notes_with_hard(self) -> int:
        return sum(1 for v in self.verdicts if v.hard)

    @property
    def notes_with_soft(self) -> int:
        return sum(1 for v in self.verdicts if v.soft)


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def check_note(
    note: str,
    listing: dict,
    tool_log: list[dict],
    *,
    max_price: int,
    max_commute: int,
    profile_numbers: frozenset[float] = frozenset(),
) -> tuple[list[str], list[str]]:
    """profile_numbers: figures the renter wrote into preferences ("at least
    600 sq ft"); quoting a threshold back is not inventing a fact."""
    hard: list[str] = []
    commute_evidence: set[int] = set()
    price_evidence: set[float] = {float(listing["price"]), float(max_price)}
    for entry in tool_log:
        result = str(entry.get("result", ""))
        if entry.get("name") == "commute_time":
            m = re.match(r"\s*(\d+)", result)
            if m:
                commute_evidence.add(int(m.group(1)))
        # Any dollar figure a tool returned is evidence (price history, and the
        # area median from compare_to_area, which the checker first missed).
        price_evidence |= {
            round(_num(x), 2) for x in re.findall(r"\$(\d[\d,]*(?:\.\d+)?)", result)
        }
    ppsf = (listing.get("details") or {}).get("price_per_sqft")
    if ppsf is not None:
        price_evidence.add(round(float(ppsf), 2))

    for m in MINUTES.finditer(note):
        minutes = int(m.group(1))
        if minutes != max_commute and minutes not in commute_evidence | profile_numbers:
            hard.append(f"invented_commute: {m.group(0)!r}")
    for m in SQFT.finditer(note):
        value = _num(m.group(1))
        if value in profile_numbers:
            continue
        if listing.get("sqft") is None or value != float(listing["sqft"]):
            hard.append(f"invented_sqft: {m.group(0)!r}")
    for m in DOLLARS.finditer(note):
        value = round(_num(m.group(1)), 2)
        if value not in price_evidence | profile_numbers:
            hard.append(f"invented_price: {m.group(0)!r}")

    lowered = note.lower()
    soft = [f"speculation: {h!r}" for h in HEDGES if h in lowered]
    return hard, soft


def profile_numbers(preferences: tuple[str, ...]) -> frozenset[float]:
    return frozenset(_num(n) for p in preferences for n in re.findall(r"\d[\d,]*", p))


def check_state(
    db_path: str,
    *,
    max_price: int,
    max_commute: int,
    preferences: tuple[str, ...] = (),
    run_id: str | None = None,
) -> GroundingReport:
    """Check every investigated note in a state database.

    Traces written before tool calls were logged fall back to the commute
    cache, which holds exactly what the routing tool returned for an address.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    query = "SELECT run_id, listing_id, detail FROM decisions WHERE action = 'investigated'"
    rows = conn.execute(
        query + (" AND run_id = ?" if run_id else ""), (run_id,) if run_id else ()
    ).fetchall()
    has_cache = bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'commute_cache'"
        ).fetchone()
    )
    verdicts = []
    for row in rows:
        detail = json.loads(row["detail"])
        listing = detail.get("listing") or _listing_from_table(conn, row["listing_id"])
        tool_log = detail.get("tool_log")
        if tool_log is None:
            tool_log = _log_from_cache(conn, listing["address"]) if has_cache else []
        hard, soft = check_note(
            detail["note"], listing, tool_log,
            max_price=max_price, max_commute=max_commute,
            profile_numbers=profile_numbers(preferences),
        )
        verdicts.append(NoteVerdict(row["run_id"], row["listing_id"], hard, soft))
    conn.close()
    return GroundingReport(verdicts)


def _listing_from_table(conn: sqlite3.Connection, listing_id: str) -> dict:
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    keys = row.keys()
    details = json.loads(row["attributes"]) if "attributes" in keys and row["attributes"] else {}
    return {"address": row["address"], "price": row["price"], "sqft": row["sqft"],
            "details": details}


def _log_from_cache(conn: sqlite3.Connection, address: str) -> list[dict]:
    rows = conn.execute(
        "SELECT minutes FROM commute_cache WHERE origin = ?", (address,)
    ).fetchall()
    return [{"name": "commute_time", "result": f"{r['minutes']} min"} for r in rows]
