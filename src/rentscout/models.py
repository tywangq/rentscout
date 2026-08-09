from __future__ import annotations

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
        )

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
        }
