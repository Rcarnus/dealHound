"""Telegram notifications, sent by analyze.py after scoring a mission.

Configuration (environment; on the server it comes from the systemd unit, locally from
config.env / secrets.env through deploy/py.sh):
    TELEGRAM_BOT_TOKEN        bot token (secret: secrets.env only)
    NOTIFY_TELEGRAM_CHAT_IDS  chat ids to notify, space-separated
    NOTIFY_MODE               top (default) | analysis | new_deal | off
    NOTIFY_TOP_N              size of the ranking watched by "top" (default 10)
A mission can override the mode in mission.yaml: notify: {mode: analysis}.

Modes:
    analysis   one message per analysis run: synthesis + current top 5 (verbose; handy
               to tune scoring criteria)
    new_deal   one message listing every listing scored in this run (verbose)
    top        only when a listing scored in this run enters the top N (default)

    python -m dealhound.notify discover   list chats that wrote to the bot (find chat ids)
    python -m dealhound.notify test       send a test message to the configured chats
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from dealhound.geo import distance_km
from dealhound.models import Mission

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/{method}"
MODES = ("top", "analysis", "new_deal", "off")
_ALIASES = {"": "top", "top10": "top", "each_analysis": "analysis", "new": "new_deal",
            "none": "off", "disabled": "off"}
MAX_MESSAGE = 3900  # Telegram limit is 4096 characters
ANALYSIS_TOP = 5
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
    """Split on blank lines (deal boundaries) to stay under the message limit."""
    out, cur = [], ""
    for block in text.split("\n\n"):
        candidate = f"{cur}\n\n{block}" if cur else block
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
            if r["status"] != "gone" and (r.get("analysis") or {}).get("score") is not None]
    recs.sort(key=lambda r: r["analysis"]["score"], reverse=True)
    return recs


def top_ids(state: dict[str, Any], n: int) -> list[str]:
    return [r["id"] for r in ranked(state)[:n]]


def _price(rec: dict[str, Any]) -> str:
    p = rec.get("price")
    if p is None:
        return "prix ?"
    s = f"{p:,.0f} €".replace(",", " ")
    hist = [h[1] for h in rec.get("price_history", []) if h[1] is not None]
    if len(hist) >= 2 and hist[-1] < hist[-2]:
        s += f" (↓ {hist[-2]:,.0f} €)".replace(",", " ")
    return s


def format_deal(mission: Mission, rec: dict[str, Any], rank: int | None, new: bool = False) -> str:
    a = rec.get("analysis") or {}
    esc = html.escape
    km = distance_km(mission.home, rec)
    place = esc(rec.get("location") or "") + (f" ({km} km)" if km is not None else "")
    head = f"{rank}. " if rank else ""
    tag = "🆕 " if new else ""
    verdict = " · <b>écartée</b>" if a.get("eliminatory") else ""
    lines = [f"{tag}<b>{head}{a.get('score', '?')}/10</b>{verdict} · {_price(rec)} · {place}",
             f'<a href="{esc(rec.get("url") or "", quote=True)}">{esc(rec.get("title") or "?")}</a>']
    scores = a.get("scores") or {}
    if scores:
        lines.append(" · ".join(f"{esc(c.label)} {scores.get(c.id, '–')}" for c in mission.scoring))
    if a.get("summary"):
        lines.append(esc(a["summary"]))
    if a.get("red_flags"):
        lines.append("⚠ " + esc("; ".join(a["red_flags"])))
    return "\n".join(lines)


def build_message(mission: Mission, state: dict[str, Any], analyzed: list[str],
                  prev_top: list[str], mode: str, top_n: int) -> str | None:
    """Message for this analysis run, or None when the mode says nothing to report."""
    if mode == "off" or not analyzed:
        return None
    esc = html.escape
    order = {r["id"]: i for i, r in enumerate(ranked(state), 1)}
    fresh = set(analyzed)
    name = f"🔎 <b>{esc(mission.name)}</b>"

    if mode == "analysis":
        top = ranked(state)[:ANALYSIS_TOP]
        parts = [f"{name} · analyse : {len(analyzed)} annonce(s) notée(s)"]
        if syn := (state.get("synthesis") or {}).get("text"):
            parts.append(esc(syn))
        parts.append(f"<b>Top {len(top)}</b>")
        parts += [format_deal(mission, r, order[r["id"]], r["id"] in fresh) for r in top]
        return "\n\n".join(parts)

    if mode == "new_deal":
        recs = sorted((state["listings"][i] for i in analyzed if i in state["listings"]),
                      key=lambda r: (r.get("analysis") or {}).get("score") or 0, reverse=True)
        parts = [f"{name} · {len(recs)} nouvelle(s) annonce(s) notée(s)"]
        parts += [format_deal(mission, r, order.get(r["id"]), True) for r in recs]
        return "\n\n".join(parts)

    # top: listings scored in this run that were not in the top N before and are now
    now_top = top_ids(state, top_n)
    entrants = [i for i in now_top if i in fresh and i not in set(prev_top)]
    if not entrants:
        return None
    parts = [f"{name} · {len(entrants)} nouveau(x) deal(s) dans le top {top_n}"]
    parts += [format_deal(mission, state["listings"][i], order[i], True) for i in entrants]
    return "\n\n".join(parts)


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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m dealhound.notify", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=("discover", "test"))
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        cfg = NotifyConfig.from_env()
        return cmd_discover(cfg) if args.cmd == "discover" else cmd_test(cfg)
    except (NotifyError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
