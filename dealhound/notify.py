"""Telegram notifications, sent by analyze.py after scoring a mission.

Configuration (environment; on the server it comes from the systemd unit, locally from
config.env / secrets.env through deploy/py.sh):
    TELEGRAM_BOT_TOKEN        bot token (secret: secrets.env only)
    NOTIFY_TELEGRAM_CHAT_IDS  chat ids to notify, space-separated
    NOTIFY_MODE               top (default) | analysis | new_deal | off
    NOTIFY_TOP_N              size of the ranking shown / watched (default 10)
A mission can override the mode in mission.yaml: notify: {mode: analysis}.

Modes (messages are compact: one deal per line, with the score of each criterion):
    analysis   after every analysis run: the current top N (handy to tune scoring)
    new_deal   after every analysis run: every listing scored in this run
    top        only when a listing scored in this run enters the top N (default)

    python -m dealhound.notify discover            chats that wrote to the bot (chat ids)
    python -m dealhound.notify test                test message to the configured chats
    python -m dealhound.notify preview <mission> [--send]
                                                   current top N as it would be notified
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from dealhound import store
from dealhound.geo import distance_km
from dealhound.models import Mission

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/{method}"
MODES = ("top", "analysis", "new_deal", "off")
_ALIASES = {"": "top", "top10": "top", "each_analysis": "analysis", "new": "new_deal",
            "none": "off", "disabled": "off"}
MAX_MESSAGE = 3900  # Telegram limit is 4096 characters
TITLE_MAX = 40      # one deal per line: keep titles short
TIMEOUT_S = 20


class NotifyError(Exception):
    pass


def normalize_mode(mode: str | None) -> str:
    m = (mode or "").strip().lower()
    m = _ALIASES.get(m, m)
    if m not in MODES:
        raise ValueError(f"unknown notify mode {mode!r}, expected one of {MODES}")
    return m


@dataclass
class NotifyConfig:
    token: str = ""
    chat_ids: list[str] = field(default_factory=list)
    mode: str = "top"
    top_n: int = 10

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "NotifyConfig":
        env = os.environ if env is None else env
        try:
            top_n = int(env.get("NOTIFY_TOP_N") or 10)
        except ValueError:
            top_n = 10
        return cls(token=(env.get("TELEGRAM_BOT_TOKEN") or "").strip(),
                   chat_ids=(env.get("NOTIFY_TELEGRAM_CHAT_IDS") or "").split(),
                   mode=normalize_mode(env.get("NOTIFY_MODE")),
                   top_n=max(1, top_n))

    def mode_for(self, mission: Mission) -> str:
        override = (mission.notify or {}).get("mode")
        return normalize_mode(override) if override else self.mode

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_ids)


# ---------------------------------------------------------------- Telegram API

def _call(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(API_URL.format(token=token, method=method),
                                 data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        # never include the URL (it contains the token) in errors
        try:
            detail = json.load(e).get("description", "")
        except (ValueError, AttributeError):
            detail = ""
        raise NotifyError(f"Telegram {method}: HTTP {e.code} {detail}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise NotifyError(f"Telegram {method}: {type(e).__name__}") from None
    if not data.get("ok"):
        raise NotifyError(f"Telegram {method}: {data.get('description', 'error')}")
    return data


def _chunks(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split on line boundaries (one deal per line) to stay under the message limit."""
    out, cur = [], ""
    for block in text.split("\n"):
        candidate = f"{cur}\n{block}" if cur else block
        if len(candidate) <= limit:
            cur = candidate
            continue
        if cur:
            out.append(cur)
        cur = block[:limit]
    if cur:
        out.append(cur)
    return out


def send(cfg: NotifyConfig, text: str) -> int:
    """Send to every configured chat. Returns the number of chats reached."""
    if not cfg.configured:
        raise NotifyError("Telegram not configured (TELEGRAM_BOT_TOKEN, NOTIFY_TELEGRAM_CHAT_IDS)")
    reached = 0
    for chat in cfg.chat_ids:
        try:
            for part in _chunks(text):
                _call(cfg.token, "sendMessage", {"chat_id": chat, "text": part,
                                                 "parse_mode": "HTML",
                                                 "disable_web_page_preview": True})
            reached += 1
        except NotifyError as e:
            log.error("notify chat %s: %s", chat, e)
    return reached


# ---------------------------------------------------------------- content

def ranked(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Active scored listings, best first (same order as /dealhound results)."""
    recs = [r for r in state["listings"].values()
            if store.is_active(r) and (r.get("analysis") or {}).get("score") is not None]
    recs.sort(key=lambda r: r["analysis"]["score"], reverse=True)
    return recs


def top_ids(state: dict[str, Any], n: int) -> list[str]:
    return [r["id"] for r in ranked(state)[:n]]


def _price(rec: dict[str, Any]) -> str:
    p = rec.get("price")
    if p is None:
        return "? €"
    s = f"{p:,.0f} €".replace(",", " ")
    hist = [h[1] for h in rec.get("price_history", []) if h[1] is not None]
    if len(hist) >= 2 and hist[-1] < hist[-2]:
        s += " ↓"
    return s


def _norm(word: str) -> str:
    return re.sub(r"[^\w.]", "", word.lower())


def _raw_title(rec: dict[str, Any]) -> str:
    """Fallback text when the analysis gave no short label: the vehicle version when the
    source has one (more regular than ad titles), else the ad title."""
    attrs = rec.get("attributes") or {}
    return attrs.get("u_car_version") or rec.get("title") or "?"


def common_words(recs: list[dict[str, Any]], share: float = 0.9) -> set[str]:
    """Words without digits found in nearly every fallback title of a message (brand,
    model…): they don't help tell the deals apart, so they are not displayed."""
    if len(recs) < 3:
        return set()
    counts: dict[str, int] = {}
    for r in recs:
        for w in {_norm(w) for w in _raw_title(r).split()}:
            if w and not any(ch.isdigit() for ch in w):
                counts[w] = counts.get(w, 0) + 1
    return {w for w, n in counts.items() if n >= share * len(recs)}


def _title(rec: dict[str, Any], drop: set[str] = frozenset()) -> str:
    label = ((rec.get("analysis") or {}).get("label") or "").strip()
    if label:
        t = label
    else:
        words = _raw_title(rec).split()
        t = " ".join([w for w in words if _norm(w) not in drop] or words).strip(" -–—·,")
    return t if len(t) <= TITLE_MAX else t[: TITLE_MAX - 1] + "…"


def format_line(mission: Mission, rec: dict[str, Any], rank: int | None, new: bool = False,
                drop: set[str] = frozenset()) -> str:
    """One deal on one line: rank, overall score, linked title, price, distance, then the
    score of each scoring criterion (short labels). Details stay in /dealhound results."""
    esc = html.escape
    a = rec.get("analysis") or {}
    score = a.get("score")
    s = f"{score:.1f}" if isinstance(score, (int, float)) else "–"
    if a.get("eliminatory"):
        s = f"<s>{s}</s>"
    km = distance_km(mission.home, rec)
    link = f'<a href="{esc(rec.get("url") or "", quote=True)}">{esc(_title(rec, drop))}</a>'
    scores = a.get("scores") or {}
    crit = " ".join(f"{esc(c.short)} {scores.get(c.id, '–')}" for c in mission.scoring)
    marks = ("🆕" if new else "") + ("⚠" if a.get("red_flags") else "")
    head = f"{rank}. " if rank else ""
    dist = f" · {km} km" if km is not None else ""
    return f"{head}<b>{s}</b> {link} · {_price(rec)}{dist} | {crit} {marks}".rstrip()


def _block(mission: Mission, header: str, recs: list[dict[str, Any]],
           order: dict[str, int], fresh: set[str]) -> str:
    legend = "🆕 nouvelle · ⚠ red flag · ↓ prix en baisse"
    drop = common_words(recs)
    lines = [format_line(mission, r, order.get(r["id"]), r["id"] in fresh, drop) for r in recs]
    return f"{header}\n<i>{legend}</i>\n\n" + "\n".join(lines)


def top_message(mission: Mission, state: dict[str, Any], n: int,
                fresh: set[str] = frozenset(), note: str = "") -> str:
    """The current top n, one deal per line."""
    ranking = ranked(state)
    order = {r["id"]: i for i, r in enumerate(ranking, 1)}
    header = f"🔎 <b>{html.escape(mission.name)}</b> · top {min(n, len(ranking))}" + (
        f" · {note}" if note else "")
    return _block(mission, header, ranking[:n], order, set(fresh))


def build_message(mission: Mission, state: dict[str, Any], analyzed: list[str],
                  prev_top: list[str], mode: str, top_n: int) -> str | None:
    """Message for this analysis run, or None when the mode says nothing to report."""
    if mode == "off" or not analyzed:
        return None
    fresh = set(analyzed)
    order = {r["id"]: i for i, r in enumerate(ranked(state), 1)}
    name = f"🔎 <b>{html.escape(mission.name)}</b>"

    if mode == "analysis":
        return top_message(mission, state, top_n, fresh, f"{len(analyzed)} annonce(s) notée(s)")

    if mode == "new_deal":
        recs = sorted((state["listings"][i] for i in analyzed if i in state["listings"]),
                      key=lambda r: (r.get("analysis") or {}).get("score") or 0, reverse=True)
        return _block(mission, f"{name} · {len(recs)} annonce(s) notée(s)", recs, order, fresh)

    # top: listings scored in this run that were not in the top N before and are now
    now_top = top_ids(state, top_n)
    entrants = [i for i in now_top if i in fresh and i not in set(prev_top)]
    if not entrants:
        return None
    return _block(mission, f"{name} · {len(entrants)} nouveau(x) dans le top {top_n}",
                  [state["listings"][i] for i in entrants], order, fresh)


def notify_after_analysis(mission: Mission, state: dict[str, Any], analyzed: list[str],
                          prev_top: list[str], cfg: NotifyConfig | None = None) -> None:
    """Best effort: a notification failure never fails the analysis."""
    try:
        cfg = cfg or NotifyConfig.from_env()
        mode = cfg.mode_for(mission)
        message = build_message(mission, state, analyzed, prev_top, mode, cfg.top_n)
        if message is None:
            log.info("[%s] notify (%s): nothing to report", mission.name, mode)
            return
        if not cfg.configured:
            log.warning("[%s] notify (%s): Telegram not configured, message not sent",
                        mission.name, mode)
            return
        reached = send(cfg, message)
        log.info("[%s] notify (%s): sent to %d/%d chat(s)", mission.name, mode,
                 reached, len(cfg.chat_ids))
    except (NotifyError, ValueError) as e:
        log.error("[%s] notify: %s", mission.name, e)


# ---------------------------------------------------------------- CLI

def cmd_discover(cfg: NotifyConfig) -> int:
    if not cfg.token:
        print("TELEGRAM_BOT_TOKEN absent : le mettre dans secrets.env", file=sys.stderr)
        return 1
    data = _call(cfg.token, "getUpdates", {"timeout": 0, "allowed_updates": ["message"]})
    chats: dict[int, str] = {}
    for upd in data.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if "id" in chat:
            label = chat.get("title") or " ".join(
                p for p in (chat.get("first_name"), chat.get("last_name")) if p)
            if chat.get("username"):
                label += f" (@{chat['username']})"
            chats[chat["id"]] = f"{chat.get('type', '?')}: {label.strip()}"
    if not chats:
        print("aucun chat : envoie /start au bot depuis chaque compte à notifier, puis relance")
        return 1
    for cid, label in chats.items():
        print(f"{cid}\t{label}")
    return 0


def cmd_test(cfg: NotifyConfig) -> int:
    reached = send(cfg, "✅ <b>dealHound</b> : test de notification.\n"
                        f"Mode : <code>{cfg.mode}</code> · top {cfg.top_n}")
    print(f"sent to {reached}/{len(cfg.chat_ids)} chat(s)")
    return 0 if reached == len(cfg.chat_ids) else 1


def cmd_preview(cfg: NotifyConfig, mission_id: str, do_send: bool) -> int:
    from pathlib import Path

    from dealhound import store
    from dealhound.models import MissionError

    if not re.fullmatch(r"[A-Za-z0-9._-]+", mission_id):
        print(f"invalid mission id: {mission_id}", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parent.parent
    try:
        mission = Mission.load(root / "missions" / mission_id)
    except MissionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    state = store.load_mission_state(mission)
    if not ranked(state):
        print("aucune annonce notée pour l'instant", file=sys.stderr)
        return 1
    text = top_message(mission, state, cfg.top_n, note="aperçu")
    if not do_send:
        print(text)
        return 0
    reached = send(cfg, text)
    print(f"sent to {reached}/{len(cfg.chat_ids)} chat(s)")
    return 0 if reached == len(cfg.chat_ids) else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m dealhound.notify", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=("discover", "test", "preview"))
    p.add_argument("mission", nargs="?", help="mission id (preview)")
    p.add_argument("--send", action="store_true", help="preview: send it instead of printing")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        cfg = NotifyConfig.from_env()
        if args.cmd == "discover":
            return cmd_discover(cfg)
        if args.cmd == "test":
            return cmd_test(cfg)
        if not args.mission:
            p.error("preview needs a mission id")
        return cmd_preview(cfg, args.mission, args.send)
    except (NotifyError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
