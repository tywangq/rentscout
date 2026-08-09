"""Hard constraints are deterministic code, never LLM judgment.

A listing the user cannot live with must be filtered even if every model call
fails — and no landlord text can talk its way past a price comparison.
"""

from __future__ import annotations

from .models import Listing
from .profile import SearchProfile


def hard_filter(
    profile: SearchProfile, listings: list[Listing]
) -> tuple[list[Listing], list[tuple[Listing, str]]]:
    passed: list[Listing] = []
    filtered: list[tuple[Listing, str]] = []
    for listing in listings:
        reason = _violation(profile, listing)
        if reason is None:
            passed.append(listing)
        else:
            filtered.append((listing, reason))
    return passed, filtered


def _violation(profile: SearchProfile, listing: Listing) -> str | None:
    if listing.price > profile.max_price:
        return f"over budget (${listing.price} > ${profile.max_price})"
    if listing.beds < profile.min_beds:
        return f"too few beds ({listing.beds} < {profile.min_beds})"
    if listing.baths < profile.min_baths:
        return f"too few baths ({listing.baths} < {profile.min_baths})"
    if profile.neighborhoods and listing.neighborhood not in profile.neighborhoods:
        return f"outside target neighborhoods ({listing.neighborhood or 'unknown'})"
    text = f"{listing.description} {listing.address}".lower()
    for keyword in profile.excluded_keywords:
        if keyword.lower() in text:
            return f"excluded keyword: {keyword!r}"
    return None
