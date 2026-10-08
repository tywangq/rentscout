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
