import dataclasses

from rentscout.filters import hard_filter
from rentscout.models import Listing


BASE = Listing(
    id="test:ok",
    source="test",
    url="",
    address="1 Pike St, Seattle, WA",
    neighborhood="Capitol Hill",
    price=2000,
    beds=1,
    baths=1,
    sqft=600,
    description="nice place with dishwasher",
    available="",
)


def variant(**kwargs) -> Listing:
    return dataclasses.replace(BASE, **kwargs)


def test_hard_filter_reasons(profile_caps):
    profile, _ = profile_caps
    listings = [
        BASE,
        variant(id="test:price", price=5000),
        variant(id="test:beds", beds=0),
        variant(id="test:baths", baths=0.5),
        variant(id="test:hood", neighborhood="Georgetown"),
        variant(id="test:keyword", description="charming BASEMENT studio"),
    ]
    passed, filtered = hard_filter(profile, listings)
    assert [l.id for l in passed] == ["test:ok"]
    reasons = {l.id: reason for l, reason in filtered}
    assert "over budget" in reasons["test:price"]
    assert "too few beds" in reasons["test:beds"]
    assert "too few baths" in reasons["test:baths"]
    assert "outside target neighborhoods" in reasons["test:hood"]
    assert "excluded keyword" in reasons["test:keyword"]  # case-insensitive


def test_empty_neighborhood_list_means_anywhere(profile_caps):
    profile, _ = profile_caps
    open_profile = dataclasses.replace(profile, neighborhoods=())
    passed, _ = hard_filter(open_profile, [variant(neighborhood="Georgetown")])
    assert len(passed) == 1
