"""Long-horizon state: SQLite, one file, plain SQL.

reconcile() is the daily heartbeat — it classifies every fetched listing as
new / price-changed / re-listed / unchanged, and (for snapshot sources) marks
missing listings delisted. Decisions and spend persist so every production run
doubles as an eval trace.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .models import Listing, attrs_from, attrs_to_json

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT,
    address TEXT,
    neighborhood TEXT,
    price INTEGER,
    beds REAL,
    baths REAL,
    sqft INTEGER,
    description TEXT,
    available TEXT,
    attributes TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_history (
    listing_id TEXT NOT NULL,
    observed_on TEXT NOT NULL,
    price INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    run_id TEXT NOT NULL,
    listing_id TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    listing_id TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('up', 'down')),
    reason TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spend_ledger (
    run_id TEXT NOT NULL,
    run_date TEXT NOT NULL,
    category TEXT NOT NULL,
    dollars REAL NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_usage (
    month TEXT NOT NULL,
    api TEXT NOT NULL,
    calls INTEGER NOT NULL,
    PRIMARY KEY (month, api)
);
CREATE TABLE IF NOT EXISTS commute_cache (
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    mode TEXT NOT NULL,
    minutes INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (origin, destination, mode)
);
CREATE TABLE IF NOT EXISTS geocode_cache (
    address TEXT PRIMARY KEY,
    lat REAL NOT NULL,
    lon REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    run_date TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    dollars REAL,
    digest_path TEXT
);
"""


@dataclass
class ReconcileResult:
    new: list[Listing] = field(default_factory=list)
    relisted: list[Listing] = field(default_factory=list)
    price_changed: list[tuple[Listing, int]] = field(default_factory=list)  # (listing, old_price)
    delisted: list[Listing] = field(default_factory=list)
    unchanged: list[Listing] = field(default_factory=list)

    @property
    def fresh(self) -> list[Listing]:
        """Listings eligible for triage: never alerted before, or back from the dead."""
        return self.new + self.relisted


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(listings)")}
        if "attributes" not in cols:  # databases created before RentCast
            self._conn.execute(
                "ALTER TABLE listings ADD COLUMN attributes TEXT NOT NULL DEFAULT '{}'"
            )

    def close(self) -> None:
        self._conn.close()

    # -- reconcile ---------------------------------------------------------

    def reconcile(
        self, run_date: str, fetched: list[Listing], *, snapshot: bool
    ) -> ReconcileResult:
        result = ReconcileResult()
        seen_ids = set()
        for listing in fetched:
            seen_ids.add(listing.id)
            row = self._conn.execute(
                "SELECT price, status FROM listings WHERE id = ?", (listing.id,)
            ).fetchone()
            if row is None:
                self._insert_listing(listing, run_date)
                result.new.append(listing)
                continue
            price_changed = row["price"] != listing.price
            self._update_listing(listing, run_date, record_price=price_changed)
            if row["status"] == "delisted":
                result.relisted.append(listing)
            elif price_changed:
                result.price_changed.append((listing, row["price"]))
            else:
                result.unchanged.append(listing)
        if snapshot:
            result.delisted = self._delist_missing(seen_ids, run_date)
        self._conn.commit()
        return result

    def _insert_listing(self, listing: Listing, run_date: str) -> None:
        self._conn.execute(
            "INSERT INTO listings (id, source, url, address, neighborhood, price,"
            " beds, baths, sqft, description, available, attributes, status,"
            " first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (
                listing.id, listing.source, listing.url, listing.address,
                listing.neighborhood, listing.price, listing.beds, listing.baths,
                listing.sqft, listing.description, listing.available,
                attrs_to_json(listing.attributes), run_date, run_date,
            ),
        )
        self._record_price(listing.id, run_date, listing.price)

    def _update_listing(
        self, listing: Listing, run_date: str, *, record_price: bool
    ) -> None:
        self._conn.execute(
            "UPDATE listings SET url = ?, address = ?, neighborhood = ?, price = ?,"
            " beds = ?, baths = ?, sqft = ?, description = ?, available = ?,"
            " attributes = ?, status = 'active', last_seen = ? WHERE id = ?",
            (
                listing.url, listing.address, listing.neighborhood, listing.price,
                listing.beds, listing.baths, listing.sqft, listing.description,
                listing.available, attrs_to_json(listing.attributes), run_date,
                listing.id,
            ),
        )
        if record_price:
            self._record_price(listing.id, run_date, listing.price)

    def _record_price(self, listing_id: str, observed_on: str, price: int) -> None:
        self._conn.execute(
            "INSERT INTO price_history (listing_id, observed_on, price) VALUES (?, ?, ?)",
            (listing_id, observed_on, price),
        )

    def _delist_missing(self, seen_ids: set[str], run_date: str) -> list[Listing]:
        rows = self._conn.execute(
            "SELECT * FROM listings WHERE status = 'active'"
        ).fetchall()
        gone = [row for row in rows if row["id"] not in seen_ids]
        for row in gone:
            self._conn.execute(
                "UPDATE listings SET status = 'delisted', last_seen = ? WHERE id = ?",
                (run_date, row["id"]),
            )
        return [self._row_to_listing(row) for row in gone]

    @staticmethod
    def _row_to_listing(row: sqlite3.Row) -> Listing:
        return Listing(
            id=row["id"], source=row["source"], url=row["url"],
            address=row["address"], neighborhood=row["neighborhood"],
            price=row["price"], beds=row["beds"], baths=row["baths"],
            sqft=row["sqft"], description=row["description"],
            available=row["available"],
            attributes=attrs_from(json.loads(row["attributes"] or "{}")),
        )

    # -- external API quota -------------------------------------------------

    def api_calls(self, month: str, api: str) -> int:
        row = self._conn.execute(
            "SELECT calls FROM api_usage WHERE month = ? AND api = ?", (month, api)
        ).fetchone()
        return row["calls"] if row else 0

    def add_api_call(self, month: str, api: str) -> None:
        self._conn.execute(
            "INSERT INTO api_usage (month, api, calls) VALUES (?, ?, 1)"
            " ON CONFLICT (month, api) DO UPDATE SET calls = calls + 1",
            (month, api),
        )
        self._conn.commit()

    def listing_attributes_by_address(self, address: str) -> dict | None:
        row = self._conn.execute(
            "SELECT attributes FROM listings WHERE address = ? ORDER BY last_seen DESC",
            (address,),
        ).fetchone()
        return json.loads(row["attributes"] or "{}") if row else None

    def area_price_per_sqft(self, zip_code: str, exclude_id: str) -> list[float]:
        """$/sqft of the other active listings in a zip, from the tracked feed."""
        values = []
        for row in self._conn.execute(
            "SELECT id, attributes FROM listings WHERE status = 'active' AND id != ?",
            (exclude_id,),
        ):
            attrs = json.loads(row["attributes"] or "{}")
            if attrs.get("zip") == zip_code and attrs.get("price_per_sqft"):
                values.append(float(attrs["price_per_sqft"]))
        return values

    # -- routing caches -----------------------------------------------------

    def cached_commute(self, origin: str, destination: str, mode: str) -> int | None:
        row = self._conn.execute(
            "SELECT minutes FROM commute_cache"
            " WHERE origin = ? AND destination = ? AND mode = ?",
            (origin, destination, mode),
        ).fetchone()
        return row["minutes"] if row else None

    def cache_commute(self, origin: str, destination: str, mode: str, minutes: int) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO commute_cache VALUES (?, ?, ?, ?, ?)",
            (origin, destination, mode, minutes, _now()),
        )
        self._conn.commit()

    def cached_geocode(self, address: str) -> tuple[float, float] | None:
        row = self._conn.execute(
            "SELECT lat, lon FROM geocode_cache WHERE address = ?", (address,)
        ).fetchone()
        return (row["lat"], row["lon"]) if row else None

    def cache_geocode(self, address: str, lat: float, lon: float) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO geocode_cache VALUES (?, ?, ?)", (address, lat, lon)
        )
        self._conn.commit()

    # -- feedback ------------------------------------------------------------

    def add_feedback(self, listing_id: str, verdict: str, reason: str = "") -> None:
        self._conn.execute(
            "INSERT INTO feedback (listing_id, verdict, reason, created_at)"
            " VALUES (?, ?, ?, ?)",
            (listing_id, verdict, reason, _now()),
        )
        self._conn.commit()

    def listing(self, listing_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()

    def feedback_map(self) -> dict[str, str]:
        """listing_id -> latest verdict."""
        rows = self._conn.execute(
            "SELECT listing_id, verdict FROM feedback ORDER BY rowid"
        ).fetchall()
        return {row["listing_id"]: row["verdict"] for row in rows}

    def rejected_ids(self) -> set[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT listing_id FROM feedback WHERE verdict = 'down'"
        ).fetchall()
        return {row["listing_id"] for row in rows}

    def price_history(self, listing_id: str) -> list[tuple[str, int]]:
        rows = self._conn.execute(
            "SELECT observed_on, price FROM price_history WHERE listing_id = ?"
            " ORDER BY observed_on",
            (listing_id,),
        ).fetchall()
        return [(row["observed_on"], row["price"]) for row in rows]

    # -- runs, decisions, spend ----------------------------------------------

    def start_run(self, run_id: str, run_date: str) -> None:
        self._conn.execute(
            "INSERT INTO runs (run_id, run_date, started_at, status)"
            " VALUES (?, ?, ?, 'running')",
            (run_id, run_date, _now()),
        )
        self._conn.commit()

    def finish_run(
        self, run_id: str, status: str, dollars: float, digest_path: str | None
    ) -> None:
        self._conn.execute(
            "UPDATE runs SET finished_at = ?, status = ?, dollars = ?,"
            " digest_path = ? WHERE run_id = ?",
            (_now(), status, dollars, digest_path, run_id),
        )
        self._conn.commit()

    def record_refused_run(self, run_id: str, run_date: str, reason: str) -> None:
        self._conn.execute(
            "INSERT INTO runs (run_id, run_date, started_at, finished_at, status)"
            " VALUES (?, ?, ?, ?, ?)",
            (run_id, run_date, _now(), _now(), f"refused: {reason}"),
        )
        self._conn.commit()

    def record_decision(
        self, run_id: str, listing_id: str | None, action: str, detail: dict | str = ""
    ) -> None:
        if isinstance(detail, dict):
            detail = json.dumps(detail)
        self._conn.execute(
            "INSERT INTO decisions (run_id, listing_id, action, detail, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (run_id, listing_id, action, detail, _now()),
        )
        self._conn.commit()

    def decisions(self, run_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM decisions WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()

    def run(self, run_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()

    def all_runs(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM runs ORDER BY started_at"
        ).fetchall()

    def record_spend(
        self, run_id: str, run_date: str, category: str, dollars: float, detail: str = ""
    ) -> None:
        self._conn.execute(
            "INSERT INTO spend_ledger (run_id, run_date, category, dollars, detail,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, run_date, category, dollars, detail, _now()),
        )
        self._conn.commit()

    def month_spend(self, month: str) -> float:
        """Total ledger dollars for a month given as 'YYYY-MM'."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(dollars), 0) AS total FROM spend_ledger"
            " WHERE run_date LIKE ?",
            (f"{month}%",),
        ).fetchone()
        return float(row["total"])
