"""ORSCommute against a fake opener: coordinates, caching, failure modes."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from rentscout.models import Listing, attrs_from
from rentscout.routing import ORSCommute
from rentscout.state import Store

ANCHOR = "2101 4th Ave, Seattle, WA"
HOME = "601 E Roy St, Seattle, WA 98102"


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Opener:
    def __init__(self, *script):
        self.script, self.urls = list(script), []

    def __call__(self, request, timeout):
        self.urls.append(request.full_url)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return Resp(json.dumps(item).encode())


GEO = {"features": [{"geometry": {"coordinates": [-122.34, 47.61]}}]}
ROUTE = {"routes": [{"summary": {"duration": 1130.0}}]}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "rs.db")
    s.reconcile("2026-10-08", [Listing(
        id="rentcast:1", source="rentcast", url="", address=HOME,
        neighborhood="Capitol Hill", price=1775, beds=1, baths=1, sqft=675,
        description="", available="",
        attributes=attrs_from({"lat": 47.625, "lon": -122.32}),
    )], snapshot=False)
    yield s
    s.close()


def _commute(store, opener):
    return ORSCommute(api_key="k", store=store, anchor=ANCHOR, opener=opener,
                      sleep=lambda s: None)


def test_uses_listing_coords_and_geocodes_only_the_anchor(store):
    opener = Opener(GEO, ROUTE)
    assert _commute(store, opener)(HOME) == "19 min by bike"
    assert "pelias/v1/search" in opener.urls[0] and "directions/cycling-regular" in opener.urls[1]
    assert len(opener.urls) == 2


def test_second_lookup_is_served_from_cache(store):
    opener = Opener(GEO, ROUTE)
    c = _commute(store, opener)
    c(HOME)
    assert c(HOME) == "19 min by bike"
    assert len(opener.urls) == 2


def test_routing_failure_degrades_to_unknown(store):
    err = urllib.error.HTTPError("u", 404, "no", {}, io.BytesIO(b"{}"))
    assert _commute(store, Opener(GEO, err))(HOME).startswith("unknown (routing HTTP 404")


def test_rate_limit_is_retried(store):
    err = urllib.error.HTTPError("u", 429, "slow", {}, io.BytesIO(b"{}"))
    assert _commute(store, Opener(GEO, err, ROUTE))(HOME) == "19 min by bike"


def test_unlocatable_address_is_unknown(store):
    opener = Opener({"features": []})
    assert _commute(store, opener)("nowhere").startswith("unknown")


def test_malformed_status_line_is_retried(store):
    # Seen live: ORS once answered with a status line http.client could not parse.
    import http.client
    opener = Opener(GEO, http.client.BadStatusLine("  "), ROUTE)
    assert _commute(store, opener)(HOME) == "19 min by bike"


def test_a_crashing_tool_becomes_an_error_message(store):
    from rentscout.budget import BudgetGuard
    from rentscout.llm import ToolCall
    from rentscout.profile import BudgetCaps
    from rentscout.tools import build_registry

    def boom(address):
        raise RuntimeError("upstream exploded")

    guard = BudgetGuard(BudgetCaps(0.05, 10, 2.0), 0.0)
    result = build_registry(store, boom).execute(
        ToolCall("commute_time", {"address": HOME}), guard
    )
    assert result.startswith("ERROR: commute_time failed (RuntimeError)")
