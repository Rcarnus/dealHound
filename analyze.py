#!/usr/bin/env python3
"""Analysis loop: score new listings (and price drops) of every mission with claude -p.

Runs on the server at a fixed interval (systemd timer, ANALYSIS_INTERVAL_MIN). A mission
with nothing new costs no inference call. Missions without scoring criteria are skipped.

    python analyze.py                          # all missions
    python analyze.py --mission <id> --limit 5 # at most 5 listings
    python analyze.py --mission <id> --dry-run # print the prompt of the first batch, no call

Model: --model, else $DEALHOUND_ANALYSIS_MODEL, else Claude Code's default.
Exit codes: 0 ok, 1 at least one mission failed, 2 bad usage/config.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dealhound import analysis, report, store
from dealhound.lock import state_lock
from dealhound.models import Mission, MissionError

ROOT = Path(__file__).resolve().parent
MISSIONS_DIR = ROOT / "missions"
BATCH_SIZE = 12

log = logging.getLogger("analyze")


def pending(state: dict) -> list[dict]:
    """Listings to (re)score: new ones and price drops, newest first."""
    recs = [r for r in state["listings"].values() if r["status"] == "new"]
    recs.sort(key=lambda r: r.get("published") or r.get("first_seen") or "", reverse=True)
    return recs


def analyze_mission(mission: Mission, args: argparse.Namespace, model: str) -> bool:
    if not mission.scoring:
        log.warning("[%s] no scoring criteria in mission.yaml, skipped", mission.name)
        return True
    state = store.load_state(mission.state_path, mission.name)
    todo = pending(state)
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        log.info("[%s] nothing to analyze", mission.name)
        return True

    batches = [todo[i:i + args.batch_size] for i in range(0, len(todo), args.batch_size)]
    log.info("[%s] %d listings to analyze in %d batch(es)", mission.name, len(todo), len(batches))

    if args.dry_run:
        print(f"===== SYSTEM =====\n{analysis.system_prompt()}\n")
        print(f"===== PROMPT (batch 1/{len(batches)}) =====\n"
              f"{analysis.build_prompt(mission, state, batches[0])}\n")
        schema = analysis.output_schema(mission.scoring, [r["id"] for r in batches[0]])
        print(f"===== SCHEMA =====\n{json.dumps(schema, ensure_ascii=False)}")
        return True

    ok = True
    total_cost = 0.0
    for i, batch in enumerate(batches, 1):
        prompt = analysis.build_prompt(mission, state, batch)
        schema = analysis.output_schema(mission.scoring, [r["id"] for r in batch])
        try:
            data, cost = analysis.run_claude(prompt, schema, model=model)
        except analysis.AnalysisError as e:
            log.error("[%s] batch %d/%d: %s", mission.name, i, len(batches), e)
            ok = False
            continue
        total_cost += cost or 0.0
        done = analysis.apply_results(state, mission, batch, data, store.now_iso(), model)
        missing = len(batch) - len(done)
        log.info("[%s] batch %d/%d: %d analyzed%s (%.3f $)", mission.name, i, len(batches),
                 len(done), f", {missing} invalid/skipped" if missing else "", cost or 0.0)
        # save after each batch: a later failure keeps the work already paid for
        state["changed_at"] = store.now_iso()
        store.save_state(mission.state_path, state)
        store.write_if_changed(mission.deals_path, report.render(mission, state))

    log.info("[%s] done, cost %.3f $", mission.name, total_cost)
    return ok


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mission", action="append", help="mission id (repeatable); default: all")
    p.add_argument("--limit", type=int, default=0, help="max listings per mission this run")
    p.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    p.add_argument("--model", default=os.environ.get("DEALHOUND_ANALYSIS_MODEL", ""))
    p.add_argument("--dry-run", action="store_true", help="print the prompt, no inference")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    if args.mission:
        dirs = [MISSIONS_DIR / m for m in args.mission]
        missing = [d.name for d in dirs if not (d / "mission.yaml").exists()]
        if missing:
            log.error("unknown mission(s): %s", ", ".join(missing))
            return 2
    else:
        dirs = sorted(d.parent for d in MISSIONS_DIR.glob("*/mission.yaml"))

    # Blocking: wait for a running collection to finish (collect.py skips while we run).
    with state_lock(blocking=not args.dry_run) as locked:
        if not locked and not args.dry_run:
            return 1
        all_ok = True
        for d in dirs:
            try:
                all_ok &= analyze_mission(Mission.load(d), args, args.model)
            except (MissionError, ValueError, OSError) as e:
                log.error("[%s] %s", d.name, e)
                all_ok = False
        return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
