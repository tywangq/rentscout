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


def test_shared_house_rooms_and_tiny_units_are_hard_filtered():
    import dataclasses

    from rentscout.filters import hard_filter
    from rentscout.models import Listing
    from rentscout.profile import load_profile
    from conftest import PROFILE

    profile, _ = load_profile(PROFILE)
    profile = dataclasses.replace(profile, max_beds=2, min_sqft=450, neighborhoods=())

    def mk(sid, beds, sqft):
        return Listing(id=f"t:{sid}", source="t", url="", address=f"{sid} St",
                       neighborhood="", price=900, beds=beds, baths=1, sqft=sqft,
                       description="", available="")

    passed, filtered = hard_filter(profile, [
        mk("room", 8, 170), mk("tiny", 1, 300), mk("ok", 1, 500), mk("nosize", 2, None),
    ])
    assert [l.id for l in passed] == ["t:ok", "t:nosize"]  # unknown size is not excluded
    reasons = dict((l.id, r) for l, r in filtered)
    assert "shared house" in reasons["t:room"] and "too small" in reasons["t:tiny"]
