"""leboncoin source.

leboncoin has no public API. The website calls an internal endpoint
(POST https://api.leboncoin.fr/finder/search) protected by DataDome; a plain
HTTP client gets a 403 captcha. Transport is delegated to the community library
`lbc` (https://github.com/etienne-hd/lbc, unofficial), which handles the
session/cookies needed to get through. Keep the volume low: personal use only.

Usage (manual test):
    python -m dealhound.sources.leboncoin "fender stratocaster" --price-max 800
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from typing import Any

from dealhound.models import Listing
from dealhound.sources.base import SearchResult, SourceError

log = logging.getLogger(__name__)

MAX_RETRIES = 2  # lbc defaults to 5 session re-inits on 403; stay gentle
PAGE_SIZE = 35
DEFAULT_MAX_PAGES = 2
PAGE_DELAY_S = (2.0, 5.0)  # random pause between pages
SORTS = ("newest", "cheapest", "relevance")


def build_search_kwargs(filters: dict[str, Any]) -> dict[str, Any]:
    """Translate mission hard filters into lbc.Client.search() arguments.

    Supported filter keys:
      query        str            keywords
      title_only   bool           search keywords in title only
      category     str            leboncoin category id ("0" = all, "30" = instruments,
                                  "2" = voitures; see lbc.Category)
      price_min    int
      price_max    int
      location     dict           {city, lat, lng, radius_km}
      sort         str            "newest" (default) | "cheapest" | "relevance"
      owner_type   str            "pro" | "private" | "all"
      enums        dict           leboncoin enum filters, e.g. {u_car_brand: [TOYOTA],
                                  u_car_model: [TOYOTA_Corolla], fuel: ["6"],
                                  vehicle_type: [break]}  (fuel 6 = hybride, 8 = rechargeable)
      ranges       dict           leboncoin range filters, e.g. {regdate: {min: 2020},
                                  mileage: {max: 160000}}
      max_pages    int            pages of 35 ads to fetch (default 2); used by search()

    Enum/range keys are the attribute keys seen in ads (see attributes of a Listing).
    """
    import lbc

    sort = filters.get("sort", "newest")
    if sort not in SORTS:
        raise ValueError(f"unknown sort {sort!r}, expected one of {SORTS}")

    kwargs: dict[str, Any] = {
        "text": filters.get("query"),
        "category": lbc.Category(str(filters.get("category", "0"))),
        "sort": {"newest": lbc.Sort.NEWEST, "cheapest": lbc.Sort.CHEAPEST,
                 "relevance": lbc.Sort.RELEVANCE}[sort],
        "search_in_title_only": bool(filters.get("title_only", False)),
        "limit": PAGE_SIZE,
    }
    if "price_min" in filters or "price_max" in filters:
        kwargs["price"] = [int(filters.get("price_min", 0)), int(filters.get("price_max", 10**9))]
    if owner := filters.get("owner_type"):
        kwargs["owner_type"] = lbc.OwnerType(owner)
    # lbc maps extra kwargs: list[str] -> enum filter, [min, max] ints -> range filter
    for key, values in (filters.get("enums") or {}).items():
        kwargs[key] = [str(v) for v in values]
    for key, bounds in (filters.get("ranges") or {}).items():
        kwargs[key] = [int(bounds.get("min", 0)), int(bounds.get("max", 10**9))]
    if loc := filters.get("location"):
        kwargs["locations"] = [lbc.City(lat=loc["lat"], lng=loc["lng"],
                                        radius=int(loc.get("radius_km", 30)) * 1000,
                                        city=loc["city"])]
    return kwargs


def parse_ad(ad: dict[str, Any]) -> Listing:
    """Map a raw leboncoin ad (as returned by the finder API) to a Listing."""
    if ad.get("price_cents") is not None:
        price: float | None = ad["price_cents"] / 100
    elif ad.get("price"):
        price = float(ad["price"][0])
    else:
        price = None

    loc = ad.get("location") or {}
    # city_label already includes the zipcode (e.g. "Lyon 69007 7e Arrondissement")
    location = loc.get("city_label") or " ".join(p for p in (loc.get("city"), loc.get("zipcode")) if p)

    attributes = {
        a["key"]: str(a.get("value_label") or a.get("value", ""))
        for a in ad.get("attributes", [])
        if "key" in a
    }
    owner = ad.get("owner") or {}
    if owner.get("type"):
        attributes["owner_type"] = str(owner["type"])  # "pro" | "private"
    if owner.get("name"):
        attributes["owner_name"] = str(owner["name"])

    return Listing(
        id=f"leboncoin:{ad['list_id']}",
        source="leboncoin",
        url=ad.get("url", ""),
        title=ad.get("subject", ""),
        price=price,
        location=location,
        published=ad.get("first_publication_date", ""),
        description=ad.get("body", ""),
        attributes=attributes,
    )


class Leboncoin:
    name = "leboncoin"

    def __init__(self, client: Any = None):
        self._client = client  # injectable for tests; created lazily otherwise

    def _get_client(self) -> Any:
        if self._client is None:
            import lbc
            self._client = lbc.Client(max_retries=MAX_RETRIES)
        return self._client

    def search(self, filters: dict[str, Any]) -> SearchResult:
        import lbc.exceptions as lbc_exc

        kwargs = build_search_kwargs(filters)
        max_pages = int(filters.get("max_pages", DEFAULT_MAX_PAGES))
        listings: dict[str, Listing] = {}
        complete = False
        for page in range(1, max_pages + 1):
            if page > 1:
                time.sleep(random.uniform(*PAGE_DELAY_S))
            try:
                result = self._get_client().search(page=page, **kwargs)
            except lbc_exc.DatadomeError as e:
                raise SourceError(f"leboncoin blocked by DataDome: {e}") from e
            except lbc_exc.LBCError as e:
                raise SourceError(f"leboncoin request failed: {e}") from e
            for ad in result.ads:
                listing = parse_ad(ad.raw)
                listings[listing.id] = listing  # dedup across pages
            log.debug("leboncoin page %d: %d ads", page, len(result.ads))
            if len(result.ads) < PAGE_SIZE:
                complete = True
                break
        return SearchResult(listings=list(listings.values()), complete=complete)


def _main() -> int:
    p = argparse.ArgumentParser(description="Manual leboncoin search test")
    p.add_argument("query")
    p.add_argument("--price-min", type=int)
    p.add_argument("--price-max", type=int)
    p.add_argument("--category", default="0")
    p.add_argument("--sort", choices=SORTS, default="newest")
    args = p.parse_args()

    filters: dict[str, Any] = {"query": args.query, "category": args.category, "sort": args.sort}
    if args.price_min is not None:
        filters["price_min"] = args.price_min
    if args.price_max is not None:
        filters["price_max"] = args.price_max

    filters["max_pages"] = 1
    try:
        result = Leboncoin().search(filters)
    except SourceError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for l in result.listings:
        print(f"{l.price!s:>8} €  {l.title[:60]:<60}  {l.location}  {l.url}")
    print(f"{len(result.listings)} annonces (complet: {result.complete})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
