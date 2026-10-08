"""Real commute times from OpenRouteService.

Listing coordinates come from the source (RentCast returns lat/lon), so the
only geocode is the profile's commute anchor, cached after the first lookup.
Route results are cached by (origin, destination, mode): a listing seen on
five days costs one routing call, not five.

ORS has no public-transit profile; the tool says which mode it measured so the
model never mistakes a bike time for a bus time.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .state import Store

# api.openrouteservice.org was shut off on 2026-09-28; HeiGIT moved every
# service under api.heigit.org/<service>/<version>/.
DIRECTIONS = "https://api.heigit.org/openrouteservice/v2/directions"
GEOCODE = "https://api.heigit.org/pelias/v1/search"
RETRYABLE = {429, 500, 502, 503, 504}
MODE_WORDS = {
    "cycling-regular": "by bike",
    "driving-car": "by car",
    "foot-walking": "on foot",
}


class ORSCommute:
    def __init__(
        self,
        *,
        api_key: str,
        store: Store,
        anchor: str,
        mode: str = "cycling-regular",
        attempts: int = 3,
        timeout: float = 15.0,
        opener=urllib.request.urlopen,
        sleep=time.sleep,
    ) -> None:
        if mode not in MODE_WORDS:
            raise ValueError(f"unsupported commute mode {mode!r}")
        self._key = api_key
        self._store = store
        self._anchor = anchor
        self._mode = mode
        self._attempts = attempts
        self._timeout = timeout
        self._open = opener
        self._sleep = sleep

    def __call__(self, address: str) -> str:
        """Tool body: minutes from address to the anchor, or an explanation."""
        cached = self._store.cached_commute(address, self._anchor, self._mode)
        if cached is not None:
            return self._format(cached)
        try:
            origin = self._listing_coords(address) or self._geocode(address)
            if origin is None:
                return "unknown (address could not be located)"
            destination = self._geocode(self._anchor)
            if destination is None:
                return "unknown (commute anchor could not be located)"
            minutes = self._route_minutes(origin, destination)
        except RoutingError as exc:
            return f"unknown ({exc})"
        self._store.cache_commute(address, self._anchor, self._mode, minutes)
        return self._format(minutes)

    def _format(self, minutes: int) -> str:
        return f"{minutes} min {MODE_WORDS[self._mode]}"

    def _listing_coords(self, address: str) -> tuple[float, float] | None:
        attrs = self._store.listing_attributes_by_address(address)
        if attrs and attrs.get("lat") is not None and attrs.get("lon") is not None:
            return float(attrs["lat"]), float(attrs["lon"])
        return None

    def _geocode(self, address: str) -> tuple[float, float] | None:
        cached = self._store.cached_geocode(address)
        if cached:
            return cached
        query = urllib.parse.urlencode(
            {"api_key": self._key, "text": address, "size": 1, "boundary.country": "US"}
        )
        data = self._request(urllib.request.Request(f"{GEOCODE}?{query}"))
        features = data.get("features") or []
        if not features:
            return None
        lon, lat = features[0]["geometry"]["coordinates"]
        self._store.cache_geocode(address, lat, lon)
        return lat, lon

    def _route_minutes(
        self, origin: tuple[float, float], destination: tuple[float, float]
    ) -> int:
        body = json.dumps(
            {"coordinates": [[origin[1], origin[0]], [destination[1], destination[0]]]}
        ).encode()
        request = urllib.request.Request(
            f"{DIRECTIONS}/{self._mode}",
            data=body,
            headers={"Authorization": self._key, "Content-Type": "application/json"},
            method="POST",
        )
        data = self._request(request)
        try:
            seconds = data["routes"][0]["summary"]["duration"]
        except (KeyError, IndexError) as exc:
            raise RoutingError("no route found") from exc
        return max(1, round(seconds / 60))

    def _request(self, request: urllib.request.Request) -> dict:
        last = ""
        for attempt in range(self._attempts):
            try:
                with self._open(request, timeout=self._timeout) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                last = f"routing HTTP {exc.code}"
                if exc.code not in RETRYABLE:
                    break
            # urllib does not wrap everything: a malformed status line or a dropped
            # connection surfaces as http.client / OSError (seen live from ORS).
            except (urllib.error.URLError, TimeoutError, http.client.HTTPException,
                    ConnectionError) as exc:
                last = f"routing network error: {exc}"
            if attempt + 1 < self._attempts:
                self._sleep(2**attempt)
        raise RoutingError(last)


class RoutingError(RuntimeError):
    pass
