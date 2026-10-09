"""RentCastSource: real active rental listings from the RentCast API.

RentCast returns structured fields only: no description, no listing URL. So
soft preferences here are about size, value, property type and freshness, and
the neighborhood comes from a coarse zip-code map.

Every HTTP attempt, retries included, is charged against a monthly request
quota in the store before it is sent. The free tier is 50 requests a month; the
cap lives in code, like the dollar cap, so a retry loop cannot burn through it.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from importlib import resources

from ..models import Listing, attrs_from
from ..state import Store

API = "rentcast"
ENDPOINT = "https://api.rentcast.io/v1/listings/rental/long-term"
MARKETS = "https://api.rentcast.io/v1/markets"
PAGE_LIMIT = 500  # RentCast's maximum per request
RETRYABLE = {429, 500, 502, 503, 504}


class RentCastError(RuntimeError):
    """The fetch failed; the run should stop rather than reconcile nothing."""


class QuotaExhausted(RentCastError):
    """Monthly RentCast request quota reached; refuse before calling."""


def _zip_map() -> dict[str, list[str]]:
    raw = json.loads(
        resources.files("rentscout.data").joinpath("seattle_zips.json").read_text()
    )
    return {k: v for k, v in raw.items() if not k.startswith("_")}


class RentCastSource:
    name = API

    def __init__(
        self,
        *,
        api_key: str,
        store: Store,
        month: str,
        monthly_cap: int,
        city: str = "Seattle",
        state: str = "WA",
        min_beds: float = 0,
        max_beds: float | None = None,
        max_price: int | None = None,
        attempts: int = 3,
        timeout: float = 20.0,
        opener=urllib.request.urlopen,
        sleep=time.sleep,
    ) -> None:
        self._key = api_key
        self._store = store
        self._month = month
        self._cap = monthly_cap
        self._params = {
            "city": city,
            "state": state,
            "status": "Active",
            "limit": str(PAGE_LIMIT),
            "includeTotalCount": "true",
        }
        if min_beds or max_beds is not None:
            upper = "" if max_beds is None else str(int(max_beds))
            self._params["bedrooms"] = f"{int(min_beds)}:{upper}"
        if max_price:
            self._params["price"] = f":{max_price}"
        self._attempts = attempts
        self._timeout = timeout
        self._open = opener
        self._sleep = sleep
        self._zips = _zip_map()
        # Only a complete result set may be treated as a snapshot; otherwise a
        # listing missing from a truncated page would be wrongly delisted.
        self.snapshot = False

    def fetch(self) -> list[Listing]:
        body, total = self._get()
        records = json.loads(body)
        self.snapshot = total is not None and total <= len(records)
        return [self._to_listing(r) for r in records]

    def market_rental_stats(self, zip_code: str) -> dict:
        """Rental market statistics for one zip (RentCast /markets): overall and
        per-bedroom median rent and rent per sqft. Same quota as listings."""
        body, _ = self._get(MARKETS, {"zipCode": zip_code, "dataType": "Rental",
                                      "historyRange": "1"})
        rental = json.loads(body).get("rentalData") or {}
        keep = ("medianRent", "medianRentPerSquareFoot", "totalListings", "lastUpdatedDate")
        return {
            **{k: rental.get(k) for k in keep},
            "byBedrooms": {
                str(b["bedrooms"]): {k: b.get(k) for k in keep if k != "lastUpdatedDate"}
                for b in rental.get("dataByBedrooms", [])
            },
        }

    def _get(self, endpoint: str = ENDPOINT, params: dict | None = None) -> tuple[str, int | None]:
        params = self._params if params is None else params
        url = f"{endpoint}?{urllib.parse.urlencode(params, safe=':')}"
        request = urllib.request.Request(
            url, headers={"X-Api-Key": self._key, "Accept": "application/json"}
        )
        last_error = ""
        for attempt in range(self._attempts):
            used = self._store.api_calls(self._month, API)
            if used >= self._cap:
                raise QuotaExhausted(
                    f"RentCast quota used: {used} of {self._cap} requests in {self._month}"
                )
            self._store.add_api_call(self._month, API)
            try:
                with self._open(request, timeout=self._timeout) as resp:
                    total = resp.headers.get("X-Total-Count")
                    return resp.read().decode(), int(total) if total else None
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}: {exc.read()[:200]!r}"
                if exc.code not in RETRYABLE:
                    break
            # urllib does not wrap everything: a malformed status line or a dropped
            # connection surfaces as http.client / OSError (seen live from ORS).
            except (urllib.error.URLError, TimeoutError, http.client.HTTPException,
                    ConnectionError) as exc:
                last_error = f"network: {exc}"
            if attempt + 1 < self._attempts:
                self._sleep(2**attempt)
        raise RentCastError(f"RentCast fetch failed: {last_error}")

    def _to_listing(self, r: dict) -> Listing:
        areas = self._zips.get(str(r.get("zipCode", "")), [])
        address = r["formattedAddress"]
        attrs = {
            "zip": r.get("zipCode"),
            "lat": r.get("latitude"),
            "lon": r.get("longitude"),
            "neighborhoods": areas,
            "property_type": r.get("propertyType"),
            "days_on_market": r.get("daysOnMarket"),
            "listed_date": (r.get("listedDate") or "")[:10],
            "year_built": r.get("yearBuilt"),
            "price_per_sqft": round(r["price"] / r["squareFootage"], 2)
            if r.get("squareFootage")
            else None,
        }
        return Listing(
            id=f"{API}:{r['id']}",
            source=API,
            url="https://www.google.com/maps/search/?api=1&query="
            + urllib.parse.quote(address),
            address=address,
            neighborhood=areas[0] if areas else "",
            price=int(r["price"]),
            beds=float(r.get("bedrooms") or 0),
            baths=float(r.get("bathrooms") or 0),
            sqft=int(r["squareFootage"]) if r.get("squareFootage") else None,
            description="",
            available="",
            attributes=attrs_from({k: v for k, v in attrs.items() if v not in (None, "", [])}),
        )
