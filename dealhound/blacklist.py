"""Per-mission blacklist: listings the user rejected (sold, unavailable, not wanted…).

File: missions/<id>/blacklist.yaml (private, like the whole missions/ folder), edited
locally (python -m dealhound.cli blacklist …) and sent to the server by deploy.sh push.

    - id: leboncoin:3268941751
      reason: déjà vendue
      at: "2026-11-09"
      title: Toyota Corolla Touring Sports Hybride 140ch Design   # for humans
      url: https://www.leboncoin.fr/ad/voitures/3268941751

A blacklisted listing stays in state.json (history) but gets a `blacklisted` flag on
load: it is no longer ranked, analyzed, shown in results or notified. Reasons are given
to the analysis so that similar listings can be scored accordingly.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml

FILENAME = "blacklist.yaml"
_HEADER = ("# Annonces écartées par l'utilisateur (vendues, indisponibles, non voulues…).\n"
           "# Géré par /dealhound blacklist ; une entrée par annonce.\n")


class BlacklistError(Exception):
    pass


def parse_ref(ref: str) -> str:
    """Listing id from an id, a URL or a bare number (leboncoin by default)."""
    ref = ref.strip()
    if re.fullmatch(r"[a-z]+:\w+", ref):
        return ref
    if m := re.search(r"leboncoin\.fr/(?:ad/[\w-]+/|[\w-]+/)?(\d{6,})", ref):
        return f"leboncoin:{m[1]}"
    if re.fullmatch(r"\d{6,}", ref):
        return f"leboncoin:{ref}"
    raise BlacklistError(f"référence d'annonce non reconnue : {ref!r} "
                         "(attendu : lien leboncoin, numéro d'annonce ou source:id)")


def path_for(mission_dir: Path) -> Path:
    return mission_dir / FILENAME


def load(mission_dir: Path) -> dict[str, dict[str, Any]]:
    path = path_for(mission_dir)
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as e:
        raise BlacklistError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(raw, list):
        raise BlacklistError(f"{path}: expected a list of entries")
    out = {}
    for entry in raw:
        if isinstance(entry, dict) and entry.get("id"):
            out[str(entry["id"])] = entry
    return out


def save(mission_dir: Path, entries: dict[str, dict[str, Any]]) -> None:
    body = yaml.safe_dump(list(entries.values()), allow_unicode=True, sort_keys=False)
    path_for(mission_dir).write_text(_HEADER + body, encoding="utf-8", newline="\n")


def add(mission_dir: Path, ref: str, reason: str,
        state: dict[str, Any] | None = None) -> dict[str, Any]:
    lid = parse_ref(ref)
    entries = load(mission_dir)
    rec = (state or {}).get("listings", {}).get(lid, {})
    entry = {"id": lid, "reason": reason.strip() or "non précisée",
             "at": date.today().isoformat()}
    if rec.get("title"):
        entry["title"] = rec["title"]
    if rec.get("url") or ref.startswith("http"):
        entry["url"] = rec.get("url") or ref
    entries[lid] = entry
    save(mission_dir, entries)
    return entry


def remove(mission_dir: Path, ref: str) -> bool:
    lid = parse_ref(ref)
    entries = load(mission_dir)
    if lid not in entries:
        return False
    del entries[lid]
    save(mission_dir, entries)
    return True


def apply(state: dict[str, Any], entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Flag blacklisted listings (and unflag those removed from the list)."""
    for lid, rec in state["listings"].items():
        if lid in entries:
            e = entries[lid]
            rec["blacklisted"] = {"reason": e.get("reason", ""), "at": str(e.get("at", ""))}
        else:
            rec.pop("blacklisted", None)
    return state
