"""Per-mission state (state.json): the source of truth for collected listings.

Shape:
{
  "version": 1,
  "mission": "<name>",
  "changed_at": "<iso>",       # last time anything in the listings changed
  "collected_at": "<iso>",     # last collection run
  "listings": {
    "<id>": {
      ...Listing fields...,
      "first_seen": "<iso>", "last_seen": "<iso>", "gone_at": null | "<iso>",
      "price_history": [["<iso>", price], ...],
      "status": "new" | "analyzed" | "gone",
      "analysis": null | {...},  # filled by analyze.py
      "blacklisted": {...}       # only when in the mission's blacklist.yaml (set on load)
    }
  }
}
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from dealhound.models import Listing

STATE_VERSION = 1

# Fields refreshed from the source on every sighting.
_REFRESHED = ("url", "title", "location", "published", "description", "attributes",
              "lat", "lng")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class MergeStats:
    new: list[str] = field(default_factory=list)
    price_drops: list[str] = field(default_factory=list)
    price_rises: list[str] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)
    back: list[str] = field(default_factory=list)  # gone, then seen again

    @property
    def changed(self) -> bool:
        return any((self.new, self.price_drops, self.price_rises, self.gone, self.back))

    def summary(self) -> str:
        return (f"{len(self.new)} new, {len(self.price_drops)} price drops, "
                f"{len(self.price_rises)} price rises, {len(self.gone)} gone, "
                f"{len(self.back)} back")


def empty_state(mission: str) -> dict[str, Any]:
    return {"version": STATE_VERSION, "mission": mission, "changed_at": None,
            "collected_at": None, "listings": {}}


def is_active(rec: dict[str, Any]) -> bool:
    """Still on sale and not rejected by the user: what rankings and analyses consider."""
    return rec["status"] != "gone" and not rec.get("blacklisted")


def load_mission_state(mission: Any) -> dict[str, Any]:
    """State of a mission with its blacklist applied (use this, not load_state)."""
    from dealhound import blacklist
    state = load_state(mission.state_path, mission.name)
    return blacklist.apply(state, blacklist.load(mission.dir))


def load_state(path: Path, mission: str) -> dict[str, Any]:
    if not path.exists():
        return empty_state(mission)
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("version") != STATE_VERSION:
        raise ValueError(f"{path}: unsupported state version {state.get('version')}")
    return state


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def write_if_changed(path: Path, text: str) -> bool:
    """Write only when content differs (spares the Pi's SD card). Returns True if written."""
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    write_atomic(path, text)
    return True


def save_state(path: Path, state: dict[str, Any]) -> None:
    write_atomic(path, json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def merge(state: dict[str, Any], source: str, listings: list[Listing],
          complete: bool, now: str) -> MergeStats:
    """Merge one source's search results into the state, in place."""
    stats = MergeStats()
    records = state["listings"]
    seen: set[str] = set()

    for listing in listings:
        seen.add(listing.id)
        rec = records.get(listing.id)
        if rec is None:
            rec = listing.to_dict()
            rec.update(first_seen=now, last_seen=now, gone_at=None,
                       price_history=[[now, listing.price]], status="new", analysis=None)
            records[listing.id] = rec
            stats.new.append(listing.id)
            continue

        for key in _REFRESHED:
            rec[key] = getattr(listing, key)
        rec["last_seen"] = now

        if rec["status"] == "gone":
            rec["status"] = "analyzed" if rec.get("analysis") else "new"
            rec["gone_at"] = None
            stats.back.append(listing.id)

        old, new = rec["price"], listing.price
        if new != old:
            rec["price"] = new
            rec["price_history"].append([now, new])
            if old is not None and new is not None and new < old:
                rec["status"] = "new"  # a price drop deserves a fresh analysis
                stats.price_drops.append(listing.id)
            else:
                stats.price_rises.append(listing.id)

    # An ad absent from the results is only "gone" if we saw every result.
    if complete:
        for lid, rec in records.items():
            if rec["source"] == source and lid not in seen and rec["status"] != "gone":
                rec["status"] = "gone"
                rec["gone_at"] = now
                stats.gone.append(lid)

    return stats
