"""Distances between the user's home (mission search center) and listings."""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import Any


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in km (straight line, not road distance)."""
    lat1, lng1, lat2, lng2 = map(radians, (*a, *b))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(h))


def distance_km(home: tuple[float, float] | None, rec: dict[str, Any]) -> int | None:
    """Rounded straight-line distance from home to a listing record, if both are known."""
    if home is None or rec.get("lat") is None or rec.get("lng") is None:
        return None
    return round(haversine_km(home, (rec["lat"], rec["lng"])))
