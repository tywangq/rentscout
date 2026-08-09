from __future__ import annotations

from typing import Protocol

from ..models import Listing


class ListingSource(Protocol):
    name: str
    # snapshot=True: each fetch returns every currently-active listing, so a
    # listing missing from the feed can safely be marked delisted.
    snapshot: bool

    def fetch(self) -> list[Listing]: ...
