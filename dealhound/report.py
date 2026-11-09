"""Render deals.md (human view) from a mission's state. Never parsed back."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from dealhound import store
from dealhound.geo import distance_km
from dealhound.models import Mission

GONE_WINDOW_DAYS = 30
MAX_TITLE = 70


def _cell(text: Any) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def _title(rec: dict[str, Any]) -> str:
    t = _cell(rec.get("title"))
    return t if len(t) <= MAX_TITLE else t[: MAX_TITLE - 1] + "…"


def _price(rec: dict[str, Any]) -> str:
    p = rec.get("price")
    if p is None:
        return "?"
    s = f"{p:,.0f} €".replace(",", " ")
    hist = [h[1] for h in rec.get("price_history", []) if h[1] is not None]
    if len(hist) >= 2 and hist[-1] < hist[-2]:
        s += f" ↓ (était {hist[-2]:,.0f} €)".replace(",", " ")
    return s


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _date(value: str | None) -> str:
    dt = _parse_dt(value)
    return dt.strftime("%d/%m %H:%M") if dt else _cell(value)


def _link(rec: dict[str, Any]) -> str:
    return f"[{rec['source']}]({rec['url']})" if rec.get("url") else rec["source"]


def _place(rec: dict[str, Any], home: tuple[float, float] | None) -> str:
    km = distance_km(home, rec)
    loc = _cell(rec.get("location"))
    return f"{loc} ({km} km)" if km is not None else loc


def render(mission: Mission, state: dict[str, Any]) -> str:
    recs = list(state["listings"].values())
    active = [r for r in recs if store.is_active(r)]
    analyzed = [r for r in active if r["status"] == "analyzed" and r.get("analysis")]
    pending = [r for r in active if r["status"] == "new"]

    changed = _parse_dt(state.get("changed_at"))
    cutoff = (changed or datetime.now().astimezone()) - timedelta(days=GONE_WINDOW_DAYS)
    gone = [r for r in recs if r["status"] == "gone"
            and (_parse_dt(r.get("gone_at")) or cutoff) >= cutoff]

    out: list[str] = [f"# Deals — {mission.name}", ""]
    if mission.description:
        out += [f"> {line}" for line in mission.description.splitlines()] + [""]
    out += [
        "*Fichier généré par dealHound, ne pas éditer.*",
        "",
        f"Dernière modification : {_date(state.get('changed_at'))} · "
        f"{len(active)} annonces actives · {len(pending)} à analyser",
        "",
    ]

    home = mission.home
    if syn := state.get("synthesis"):
        out += [f"## Synthèse ({_date(syn.get('at'))})", "", _cell(syn.get("text")), ""]

    if mission.scoring:
        out += ["## Critères de notation", "",
                "| Critère | Poids | Barème |", "|---|---:|---|"]
        out += [f"| {_cell(c.label)} | {c.weight:g} | {_cell(c.description)} |"
                for c in mission.scoring]
        out.append("")

    if analyzed:
        analyzed.sort(key=lambda r: r["analysis"].get("score", 0), reverse=True)
        crit = mission.scoring
        out += ["## Meilleurs deals", "",
                "| Score | " + " | ".join(_cell(c.label) for c in crit)
                + " | Prix | Annonce | Lieu | Avis | Lien |",
                "|---:|" + "---:|" * len(crit) + "---:|---|---|---|---|"]
        for r in analyzed:
            a = r["analysis"]
            scores = a.get("scores") or {}
            avis = _cell(a.get("summary"))
            if a.get("eliminatory"):
                avis = "**Écartée.** " + avis
            if a.get("red_flags"):
                avis += " ⚠ " + _cell("; ".join(a["red_flags"]))
            out.append(f"| **{_cell(a.get('score'))}** | "
                       + " | ".join(_cell(scores.get(c.id, "-")) for c in crit)
                       + f" | {_price(r)} | {_title(r)} | {_place(r, home)} | {avis} | {_link(r)} |")
        out.append("")

    out += [f"## À analyser ({len(pending)})", ""]
    if pending:
        pending.sort(key=lambda r: r.get("published") or r["first_seen"], reverse=True)
        out += ["| Prix | Annonce | Lieu | Publiée | Lien |", "|---:|---|---|---|---|"]
        for r in pending:
            out.append(f"| {_price(r)} | {_title(r)} | {_place(r, home)} | "
                       f"{_date(r.get('published'))} | {_link(r)} |")
    else:
        out.append("Rien de nouveau.")
    out.append("")

    if gone:
        gone.sort(key=lambda r: r.get("gone_at") or "", reverse=True)
        out += [f"## Disparues ({GONE_WINDOW_DAYS} derniers jours)", "",
                "| Prix | Annonce | Disparue le | Lien |", "|---:|---|---|---|"]
        for r in gone:
            out.append(f"| {_price(r)} | {_title(r)} | {_date(r.get('gone_at'))} | {_link(r)} |")
        out.append("")

    black = [r for r in recs if r.get("blacklisted") and r["status"] != "gone"]
    if black:
        out += [f"## Liste noire ({len(black)})", "",
                "| Prix | Annonce | Raison | Lien |", "|---:|---|---|---|"]
        for r in black:
            out.append(f"| {_price(r)} | {_title(r)} | {_cell(r['blacklisted'].get('reason'))} "
                       f"| {_link(r)} |")
        out.append("")

    return "\n".join(out)
