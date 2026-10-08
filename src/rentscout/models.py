from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Listing:
    id: str  # globally unique: "<source>:<source_id>"
    source: str
    url: str
    address: str
    neighborhood: str
    price: int  # monthly rent, USD
    beds: float
    baths: float
    sqft: int | None
    description: str
    available: str  # ISO date, or "" if unknown
    # Structured facts from sources that carry no free text (RentCast): sorted
    # (key, value) pairs so the dataclass stays frozen and hashable.
    attributes: tuple[tuple[str, object], ...] = ()

    @classmethod
    def from_dict(cls, source: str, d: dict) -> "Listing":
        return cls(
            id=f"{source}:{d['source_id']}",
            source=source,
            url=d.get("url", ""),
            address=d["address"],
            neighborhood=d.get("neighborhood", ""),
            price=int(d["price"]),
            beds=float(d["beds"]),
            baths=float(d["baths"]),
            sqft=int(d["sqft"]) if d.get("sqft") is not None else None,
            description=d.get("description", ""),
            available=d.get("available", ""),
            attributes=attrs_from(d.get("attributes") or {}),
        )

    @property
    def attrs(self) -> dict:
        return dict(self.attributes)

    def area_names(self) -> set[str]:
        """Every neighborhood this listing may belong to (zip codes span several)."""
        return {self.neighborhood, *self.attrs.get("neighborhoods", ())} - {""}

    def public_fields(self) -> dict:
        """The subset shown to the LLM. description is untrusted landlord text."""
        return {
            "id": self.id,
            "address": self.address,
            "neighborhood": self.neighborhood,
            "price": self.price,
            "beds": self.beds,
            "baths": self.baths,
            "sqft": self.sqft,
            "description": self.description,
            "available": self.available,
            **({"details": self.attrs} if self.attributes else {}),
        }


def attrs_from(d: dict) -> tuple[tuple[str, object], ...]:
    return tuple(
        sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in d.items())
    )


def attrs_to_json(attributes: tuple[tuple[str, object], ...]) -> str:
    return json.dumps(
        {k: list(v) if isinstance(v, tuple) else v for k, v in attributes}
    )
