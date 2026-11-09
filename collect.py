#!/usr/bin/env python3
"""Collection loop: query every source of every mission, update state.json and deals.md.

No LLM involved. Meant to run often (cron / systemd timer).

    python collect.py                      # all missions in missions/
    python collect.py --mission <nom>
    python collect.py --dry-run -v         # query sources, print changes, write nothing

Exit codes: 0 ok, 1 at least one source or mission failed, 2 bad usage/config.
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from pathlib import Path

from dealhound import report, store
from dealhound.lock import state_lock
from dealhound.models import Mission, MissionError
from dealhound.sources import REGISTRY
from dealhound.sources.base import SourceError

ROOT = Path(__file__).resolve().parent
MISSIONS_DIR = ROOT / "missions"
BETWEEN_QUERIES_S = (3.0, 8.0)  # random pause between two source queries

log = logging.getLogger("collect")


def find_missions(names: list[str] | None) -> list[Path]:
    if names:
        dirs = [MISSIONS_DIR / n for n in names]
        missing = [d.name for d in dirs if not (d / "mission.yaml").exists()]
        if missing:
            raise MissionError(f"unknown mission(s): {', '.join(missing)}")
        return dirs
    return sorted(p.parent for p in MISSIONS_DIR.glob("*/mission.yaml"))


def collect_mission(mission: Mission, dry_run: bool, first_query: list[bool]) -> bool:
    """Returns True if every source succeeded."""
    state = store.load_mission_state(mission)
    now = store.now_iso()
    ok = True
    any_change = False

    for source_name in mission.sources:
        factory = REGISTRY.get(source_name)
        if factory is None:
            log.error("[%s] unknown source %r (known: %s)", mission.name, source_name,
                      ", ".join(REGISTRY))
            ok = False
            continue

        if not first_query[0]:
            time.sleep(random.uniform(*BETWEEN_QUERIES_S))
        first_query[0] = False

        try:
            result = factory().search(mission.filters_for(source_name))
        except SourceError as e:
            log.error("[%s] %s: %s", mission.name, source_name, e)
            ok = False
            continue

        stats = store.merge(state, source_name, result.listings, result.complete, now)
        any_change |= stats.changed
        log.info("[%s] %s: %d ads (complete=%s) -> %s", mission.name, source_name,
                 len(result.listings), result.complete, stats.summary())
        for lid in stats.new + stats.price_drops:
            rec = state["listings"][lid]
            log.debug("  %s %s € %s", "NEW " if lid in stats.new else "DROP",
                      rec["price"], rec["title"])

    state["collected_at"] = now
    if any_change or not state.get("changed_at"):
        state["changed_at"] = now

    if dry_run:
        log.info("[%s] dry run: nothing written", mission.name)
        return ok

    mission.dir.mkdir(parents=True, exist_ok=True)
    store.save_state(mission.state_path, state)
    if store.write_if_changed(mission.deals_path, report.render(mission, state)):
        log.info("[%s] deals.md updated", mission.name)
    return ok


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mission", action="append", help="mission name (repeatable); default: all")
    p.add_argument("--dry-run", action="store_true", help="query sources but write nothing")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    for noisy in ("curl_cffi", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        mission_dirs = find_missions(args.mission)
    except MissionError as e:
        log.error("%s", e)
        return 2
    if not mission_dirs:
        log.warning("no mission found in %s", MISSIONS_DIR)
        return 0

    with state_lock(blocking=False) as locked:
        if not locked:
            log.warning("a collection or an analysis is running, skipping this run")
            return 0

        all_ok = True
        first_query = [True]
        for d in mission_dirs:
            try:
                mission = Mission.load(d)
                all_ok &= collect_mission(mission, args.dry_run, first_query)
            except (MissionError, ValueError, OSError) as e:
                log.error("[%s] %s", d.name, e)
                all_ok = False
        return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
