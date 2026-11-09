"""Helpers behind the /dealhound skill and deploy.sh (read-only, except blacklist).

Run on the machine that holds the missions (locally, or on the server via deploy.sh):

    python -m dealhound.cli list [--json]                 missions, criteria, counts
    python -m dealhound.cli results <mission> [-n 10] [--json]   best deals of a mission
    python -m dealhound.cli doctor [--json]               local configuration check
    python -m dealhound.cli blacklist <mission> list|add|remove [ref] [--reason R]

Exit codes: 0 ok, 1 failure (doctor: at least one FAIL), 2 bad usage / unknown mission.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from dealhound import store
from dealhound.geo import distance_km
from dealhound.models import Mission, MissionError

ROOT = Path(__file__).resolve().parent.parent
MISSIONS_DIR = ROOT / "missions"
CONTEXT_DIR = ROOT / "context"
CONFIG_PATH = ROOT / "config.env"
SECRETS_PATH = ROOT / "secrets.env"
CONTEXT_FILES = ("profil.md", "preferences.md", "achats.md", "communication.md")
REQUIRED_CONFIG = ("DEPLOY_HOST", "DEPLOY_DIR")

# Attributes worth surfacing in a results table, when the source provides them.
HIGHLIGHT_KEYS = ("u_car_version", "regdate", "mileage", "condition", "brand",
                  "owner_type", "car_price_positioning")


# ---------------------------------------------------------------- list

def criteria(mission: Mission) -> dict[str, dict[str, Any]]:
    """Search criteria: hard filters per source, condensed for display."""
    out: dict[str, dict[str, Any]] = {}
    for source in mission.sources:
        f = mission.filters_for(source)
        c: dict[str, Any] = {}
        if f.get("query"):
            c["query"] = f["query"]
        if "price_min" in f or "price_max" in f:
            c["price"] = [f.get("price_min"), f.get("price_max")]
        if loc := f.get("location"):
            c["location"] = f"{loc.get('city', '?')} +{loc.get('radius_km', 30)} km"
        for key in ("category", "owner_type", "enums", "ranges"):
            if f.get(key):
                c[key] = f[key]
        out[source] = c
    return out


def mission_dirs() -> list[Path]:
    return sorted(p.parent for p in MISSIONS_DIR.glob("*/mission.yaml"))


def summarize(mission_dir: Path) -> dict[str, Any]:
    try:
        m = Mission.load(mission_dir)
        state = store.load_mission_state(m)
    except (MissionError, ValueError, OSError) as e:
        return {"id": mission_dir.name, "error": str(e)}
    recs = list(state["listings"].values())
    count = lambda status: sum(1 for r in recs if r["status"] == status)  # noqa: E731
    return {
        "id": mission_dir.name,
        "name": m.name,
        "file": f"missions/{mission_dir.name}/mission.yaml",
        "description": m.description,
        "search_criteria": criteria(m),
        "scoring_criteria": [c.to_dict() for c in m.scoring],
        "has_scoring_prompt": bool(m.scoring_prompt.strip()),
        "has_context": bool(m.context.strip()),
        "collected_at": state.get("collected_at"),
        "changed_at": state.get("changed_at"),
        "counts": {"new": count("new"), "analyzed": count("analyzed"), "gone": count("gone")},
    }


def cmd_list(args: argparse.Namespace) -> int:
    missions = [summarize(d) for d in mission_dirs()]
    if args.json:
        print(json.dumps(missions, ensure_ascii=False))
        return 0
    if not missions:
        print("aucune mission")
    for m in missions:
        if "error" in m:
            print(f"{m['id']}: ERREUR {m['error']}")
            continue
        c = m["counts"]
        print(f"{m['id']}: {m['description'][:100]}")
        print(f"  collecte {m['collected_at'] or 'jamais'} | {c['new']} à analyser, "
              f"{c['analyzed']} analysées, {c['gone']} disparues")
        for source, crit in m["search_criteria"].items():
            print(f"  recherche {source}: {json.dumps(crit, ensure_ascii=False)}")
        notation = ", ".join(f"{s['label']} ×{s['weight']:g}" for s in m["scoring_criteria"])
        print(f"  notation: {notation or 'aucun critère'}")
    return 0


# ---------------------------------------------------------------- results

def _sort_key(rec: dict[str, Any]) -> tuple:
    score = (rec.get("analysis") or {}).get("score")
    return (score is not None, score if score is not None else 0,
            rec.get("published") or rec.get("first_seen") or "")


def top_deals(state: dict[str, Any], n: int,
              home: tuple[float, float] | None = None) -> list[dict[str, Any]]:
    """Active listings, best first: analyzed by score, then unanalyzed by recency.

    A listing back in "new" after a price drop keeps its previous analysis, flagged
    as stale until re-scored."""
    active = [r for r in state["listings"].values() if store.is_active(r)]
    active.sort(key=_sort_key, reverse=True)
    rows = []
    for rank, r in enumerate(active[:n], 1):
        prices = [p for _, p in r.get("price_history", []) if p is not None]
        a = r.get("analysis") or {}
        rows.append({
            "rank": rank,
            "id": r["id"],
            "score": a.get("score"),
            "scores": a.get("scores") or {},
            "eliminatory": a.get("eliminatory", False),
            "analysis_stale": bool(a) and r["status"] == "new",
            "label": a.get("label"),
            "summary": a.get("summary"),
            "red_flags": a.get("red_flags") or [],
            "questions": a.get("questions") or [],
            "price": r.get("price"),
            "previous_price": prices[-2] if len(prices) >= 2 and prices[-1] < prices[-2] else None,
            "title": r.get("title"),
            "location": r.get("location"),
            "distance_km": distance_km(home, r),
            "published": r.get("published"),
            "first_seen": r.get("first_seen"),
            "url": r.get("url"),
            "highlights": {k: r["attributes"][k] for k in HIGHLIGHT_KEYS
                           if r.get("attributes", {}).get(k)},
        })
    return rows


def cmd_results(args: argparse.Namespace) -> int:
    d = MISSIONS_DIR / args.mission
    if not (d / "mission.yaml").exists():
        print(f"mission inconnue : {args.mission}", file=sys.stderr)
        return 2
    m = Mission.load(d)
    state = store.load_mission_state(m)
    recs = state["listings"].values()
    out = {
        "id": args.mission,
        "description": m.description,
        "search_criteria": criteria(m),
        "scoring_criteria": [c.to_dict() for c in m.scoring],
        "synthesis": state.get("synthesis"),
        "collected_at": state.get("collected_at"),
        "active": sum(1 for r in recs if store.is_active(r)),
        "blacklisted": sum(1 for r in recs if r.get("blacklisted")),
        "analyzed": sum(1 for r in recs if r["status"] == "analyzed"),
        "pending": sum(1 for r in recs if r["status"] == "new"),
        "deals": top_deals(state, args.n, m.home),
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
        return 0
    print(f"{out['id']} : {out['active']} annonces actives, {out['analyzed']} analysées "
          f"(collecte {out['collected_at'] or 'jamais'})")
    crit = out["scoring_criteria"]
    print("notation : " + (", ".join(f"{c['id']}={c['label']} ×{c['weight']:g}" for c in crit)
                           or "aucun critère"))
    for row in out["deals"]:
        score = f"{row['score']:>4}" if row["score"] is not None else "   -"
        detail = " ".join(f"{c['id']}:{row['scores'].get(c['id'], '-')}" for c in crit) if row["scores"] else ""
        km = f" ({row['distance_km']} km)" if row["distance_km"] is not None else ""
        print(f"{row['rank']:>2}. {score} {row['price']!s:>9} €  {(row['title'] or '')[:50]:<50} "
              f"{row['location'] or ''}{km}  {detail}  {row['url']}")
    return 0


# ---------------------------------------------------------------- doctor

def read_config(path: Path = CONFIG_PATH) -> dict[str, str]:
    """Parse the KEY="value" lines of config.env (no shell expansion)."""
    conf: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if m := re.match(r'^\s*([A-Z_][A-Z0-9_]*)=(.*)$', line):
            try:
                words = shlex.split(m[2], comments=True)
            except ValueError:
                words = [m[2].strip()]
            conf[m[1]] = " ".join(words)
    return conf


def notify_checks(conf: dict[str, str], add) -> None:
    from dealhound.notify import normalize_mode
    try:
        mode = normalize_mode(conf.get("NOTIFY_MODE"))
    except ValueError as e:
        add("NOTIFY_MODE", "FAIL", str(e))
        return
    top_n = conf.get("NOTIFY_TOP_N", "10")
    add("NOTIFY_MODE", "ok", f"{mode}" + (f" (top {top_n})" if mode == "top" else ""))
    if mode == "off":
        return
    chats = conf.get("NOTIFY_TELEGRAM_CHAT_IDS", "").split()
    if not chats:
        add("NOTIFY_TELEGRAM_CHAT_IDS", "warn", "empty: no notification will be sent "
            "(wizard, step Telegram)")
    elif not all(re.fullmatch(r"-?\d+", c) for c in chats):
        add("NOTIFY_TELEGRAM_CHAT_IDS", "FAIL", "chat ids must be numbers, space-separated")
    else:
        add("NOTIFY_TELEGRAM_CHAT_IDS", "ok", f"{len(chats)} chat(s)")
    token = read_config(SECRETS_PATH).get("TELEGRAM_BOT_TOKEN", "") if SECRETS_PATH.exists() else ""
    if not token:
        add("secrets.env", "warn", "no TELEGRAM_BOT_TOKEN (cp secrets.example.env secrets.env)")
    elif not re.fullmatch(r"\d+:[A-Za-z0-9_-]{30,}", token):
        add("secrets.env", "FAIL", "TELEGRAM_BOT_TOKEN does not look like a bot token")
    else:
        add("secrets.env", "ok", "TELEGRAM_BOT_TOKEN present (value not shown)")


def doctor() -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    add = lambda name, status, detail="": checks.append(  # noqa: E731
        {"check": name, "status": status, "detail": detail})

    if CONFIG_PATH.exists():
        conf = read_config()
        missing = [k for k in REQUIRED_CONFIG if not conf.get(k)]
        if missing:
            add("config.env", "FAIL", f"missing: {', '.join(missing)}")
        elif conf["DEPLOY_HOST"] == "user@192.168.1.10":
            add("config.env", "FAIL", "DEPLOY_HOST still has the example value")
        else:
            hosts = " ".join([conf["DEPLOY_HOST"], conf.get("EXTRA_HOSTS", "")]).split()
            add("config.env", "ok", f"servers: {', '.join(hosts)}")
        for key, default in (("SEARCH_INTERVAL_MIN", 30), ("ANALYSIS_INTERVAL_MIN", 180)):
            value = conf.get(key, "")
            if not value:
                add(key, "warn", f"not set, default {default} min")
            elif not value.isdigit() or int(value) < 5:
                add(key, "FAIL", f"{value!r}: must be a number of minutes >= 5")
            else:
                add(key, "ok", f"{value} min")
        add("ANALYSIS_MODEL", "ok", conf.get("ANALYSIS_MODEL") or "Claude Code default")
        notify_checks(conf, add)
    else:
        add("config.env", "FAIL", "absent: run /dealhound wizard")

    v = sys.version_info
    add("python", "ok" if v >= (3, 10) else "FAIL", f"{v.major}.{v.minor}.{v.micro} ({sys.executable})")
    for mod in ("lbc", "yaml"):
        try:
            importlib.import_module(mod)
            add(f"module {mod}", "ok")
        except ImportError as e:
            add(f"module {mod}", "FAIL", f"{e}: pip install -r requirements.txt")

    missing_ctx = [f for f in CONTEXT_FILES if not (CONTEXT_DIR / f).exists()]
    add("context/", "warn" if missing_ctx else "ok",
        f"missing: {', '.join(missing_ctx)}" if missing_ctx else ", ".join(CONTEXT_FILES))

    dirs = mission_dirs()
    bad, unscored = [], []
    for d in dirs:
        try:
            m = Mission.load(d)
        except MissionError as e:
            bad.append(f"{d.name} ({e})")
            continue
        if not m.scoring or not m.scoring_prompt.strip():
            unscored.append(d.name)
    if bad:
        add("missions", "FAIL", "invalid: " + "; ".join(bad))
    else:
        add("missions", "ok", f"{len(dirs)} local: {', '.join(d.name for d in dirs) or 'none'}")
    if unscored:
        add("scoring", "warn", "no scoring criteria or scoring.md (not analyzed): "
            + ", ".join(unscored))
    return checks


def cmd_doctor(args: argparse.Namespace) -> int:
    checks = doctor()
    if args.json:
        print(json.dumps(checks, ensure_ascii=False))
    else:
        for c in checks:
            print(f"  {c['status']:<5} {c['check']}: {c['detail']}")
    return 1 if any(c["status"] == "FAIL" for c in checks) else 0


# ---------------------------------------------------------------- blacklist

def cmd_blacklist(args: argparse.Namespace) -> int:
    from dealhound import blacklist

    d = MISSIONS_DIR / args.mission
    if not (d / "mission.yaml").exists():
        print(f"mission inconnue : {args.mission}", file=sys.stderr)
        return 2
    try:
        if args.action == "list":
            entries = list(blacklist.load(d).values())
            if args.json:
                print(json.dumps(entries, ensure_ascii=False))
            else:
                for e in entries or [{"id": "(liste noire vide)"}]:
                    print(f"{e['id']}  {e.get('at', '')}  {e.get('reason', '')}  {e.get('title', '')}")
            return 0
        if not args.ref:
            print("référence d'annonce requise (lien, numéro ou source:id)", file=sys.stderr)
            return 2
        if args.action == "add":
            m = Mission.load(d)
            state = store.load_state(m.state_path, m.name)
            entry = blacklist.add(d, args.ref, args.reason, state)
            if args.title and "title" not in entry or args.url and "url" not in entry:
                entries = blacklist.load(d)
                entries[entry["id"]].update({k: v for k, v in (("title", args.title),
                                             ("url", args.url)) if v})
                blacklist.save(d, entries)
                entry = entries[entry["id"]]
            print(json.dumps(entry, ensure_ascii=False) if args.json
                  else f"ajoutée : {entry['id']} ({entry['reason']})")
            return 0
        removed = blacklist.remove(d, args.ref)
        print("retirée" if removed else "absente de la liste noire")
        return 0 if removed else 1
    except blacklist.BlacklistError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m dealhound.cli", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help="missions, criteria and counts")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("results", help="best deals of a mission")
    s.add_argument("mission")
    s.add_argument("-n", type=int, default=10)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_results)

    s = sub.add_parser("doctor", help="local configuration check")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("blacklist", help="listings rejected by the user (local mission file)")
    s.add_argument("mission")
    s.add_argument("action", choices=("list", "add", "remove"))
    s.add_argument("ref", nargs="?", help="listing URL, number or source:id (add/remove)")
    s.add_argument("--reason", default="", help="why it is rejected (add)")
    s.add_argument("--title", default="", help="listing title, if not in the local state (add)")
    s.add_argument("--url", default="", help="listing URL, if not in the local state (add)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_blacklist)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
