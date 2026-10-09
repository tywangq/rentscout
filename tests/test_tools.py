"""compare_to_area: a free tool computed from the already-fetched feed."""

from __future__ import annotations

from rentscout.budget import BudgetGuard
from rentscout.llm import ToolCall
from rentscout.models import Listing, attrs_from
from rentscout.profile import BudgetCaps
from rentscout.tools import build_registry


def mk(i, zip_code, price, sqft):
    attrs = {"zip": zip_code}
    if sqft:
        attrs["price_per_sqft"] = round(price / sqft, 2)
    return Listing(id=f"t:{i}", source="t", url="", address=f"{i} St", neighborhood="",
                   price=price, beds=1, baths=1, sqft=sqft, description="",
                   available="", attributes=attrs_from(attrs))


def run(store, listing_id):
    guard = BudgetGuard(BudgetCaps(0.05, 0, 2.0), 0.0)  # zero metered quota
    return build_registry(store, {}).execute(
        ToolCall("compare_to_area", {"listing_id": listing_id}), guard)


def test_compares_against_the_same_zip_median_and_is_free(store):
    peers = [mk(i, "98105", 2000, 600) for i in range(4)]  # $3.33/sqft each
    other_zip = [mk(9, "98122", 9000, 600)]
    store.reconcile("2026-10-08", peers + other_zip + [mk("me", "98105", 1500, 600)],
                    snapshot=False)
    result = run(store, "t:me")
    assert "4 other tracked listings in 98105" in result
    assert "median $3.33/sqft" in result and "25% below" in result


def test_too_few_peers_or_no_size_says_so(store):
    store.reconcile("2026-10-08", [mk(1, "98105", 2000, 600), mk("me", "98105", 1500, None)],
                    snapshot=False)
    assert "too few to compare" in run(store, "t:me")


def test_triage_payload_carries_the_area_median(store):
    """Triage once marked 'good value' a listing 5% above its zip's median,
    because it had no area data; the pipeline now sends it."""
    import json as _json

    from rentscout.llm import LLMReply, Usage
    from rentscout.pipeline import _area_context
    from rentscout.profile import load_profile
    from rentscout.triage import triage
    from conftest import PROFILE

    peers = [mk(i, "98105", 2000, 600) for i in range(4)]
    me = mk("me", "98105", 2100, 600)
    store.reconcile("2026-10-08", peers + [me], snapshot=False)

    seen = {}

    class Capture:
        model = "scripted-fake"

        def complete(self, messages, tools=None, schema=None):
            seen["payload"] = messages[-1]["content"]
            return LLMReply(text='{"listings": []}', usage=Usage(10, 10))

    profile, caps = load_profile(PROFILE)
    triage(Capture(), BudgetGuard(caps, 0.0), profile, [me], area=_area_context(store, [me]))
    payload = _json.loads(seen["payload"].split("```json\n", 1)[1].rsplit("\n```", 1)[0])
    area = payload["listings"][0]["area"]
    assert area["median_price_per_sqft"] == 3.33 and area["vs_median_pct"] == 5
