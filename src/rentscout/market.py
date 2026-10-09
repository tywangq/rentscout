"""RentCast market statistics for the profile's target zip codes, cached monthly.

Why: "good value for the area" was judged against the median of listings this
agent had already fetched, and that feed is pre-filtered to the profile's price
and bedroom limits, so its median is biased low and mixes studios with
two-bedrooms. RentCast's /markets returns the whole zip's rental market, split
by bedroom count (98105: 382 listings; the tracked feed had 46).

Budget: these requests share the monthly RentCast quota with the daily listing
fetch, which must never be starved. A refresh only spends what is left after
reserving one request for every remaining day of the month, today included;
zips it cannot afford keep last month's numbers or fall back to the feed median.
"""

from __future__ import annotations

import calendar
import json
from datetime import date
from importlib import resources

from .profile import SearchProfile
from .sources.rentcast import API, RentCastError
from .state import Store


def target_zips(profile: SearchProfile) -> list[str]:
    """Zip codes whose neighborhoods overlap the profile's target neighborhoods."""
    raw = json.loads(
        resources.files("rentscout.data").joinpath("seattle_zips.json").read_text()
    )
    wanted = set(profile.neighborhoods)
    return [z for z, areas in raw.items() if not z.startswith("_") and wanted & set(areas)]


def affordable_requests(store: Store, today: date, monthly_cap: int) -> int:
    month = today.isoformat()[:7]
    last_day = calendar.monthrange(today.year, today.month)[1]
    reserved = last_day - today.day + 1  # one listing fetch per remaining day, today included
    return max(0, monthly_cap - store.api_calls(month, API) - reserved)


def refresh_market_stats(source, store: Store, profile: SearchProfile, today: date,
                         monthly_cap: int) -> list[str]:
    """Fetch this month's stats for stale target zips, within the spare quota."""
    month = today.isoformat()[:7]
    stale = [z for z in target_zips(profile)
             if (store.market_stats(z) or {}).get("month") != month]
    budget = affordable_requests(store, today, monthly_cap)
    fetched = []
    for zip_code in stale[:budget]:
        try:
            store.save_market_stats(zip_code, month, source.market_rental_stats(zip_code))
        except RentCastError:
            break  # quota or network: keep what we have; the feed median is the fallback
        fetched.append(zip_code)
    return fetched
