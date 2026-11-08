"""Interface every source module implements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from dealhound.models import Listing


class SourceError(Exception):
    """Raised when a source cannot be queried (blocked, format change, network)."""


@dataclass
class SearchResult:
    listings: list[Listing]
    # True when every matching ad was returned (the last page was not full).
    # Only then can an ad missing from the results be considered gone.
    complete: bool


class Source(Protocol):
    name: str

    def search(self, filters: dict[str, Any]) -> SearchResult:
        """Return listings matching the mission's hard filters for this source."""
        ...
