"""Scoring of listings by inference (claude -p), used by analyze.py.

The prompt = prompts/analyze.md (system) + user/mission context + the mission's scoring
prompt (scoring.md) and criteria + best listings so far + the listings to score.
Claude returns per-criterion scores (structured output, validated by a JSON schema);
the overall score is computed here as a weighted mean.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from dealhound.context import build_context
from dealhound.geo import distance_km
from dealhound.models import Criterion, Mission

ROOT = Path(__file__).resolve().parent.parent
SYSTEM_PROMPT_PATH = ROOT / "prompts" / "analyze.md"

ELIMINATORY_CAP = 2.0
DESCRIPTION_MAX = 2000
CLAUDE_TIMEOUT_S = 600

# Attributes that carry no information for scoring (display/marketing/plumbing).
_NOISE_ATTRIBUTES = ("logo", "picture", "online_store_id", "custom_ref", "argus_object_id",
                     "has_visibility_option", "spare_parts_availability",
                     "licence_plate_available", "monthly_payment", "financing_partner",
                     "activity_sector", "rating_", "vehicle_is_eligible_p2p")


class AnalysisError(Exception):
    pass


# ---------------------------------------------------------------- scoring

def overall_score(scores: dict[str, int], criteria: list[Criterion], eliminatory: bool) -> float:
    total_w = sum(c.weight for c in criteria)
    score = sum(scores[c.id] * c.weight for c in criteria) / total_w
    if eliminatory:
        score = min(score, ELIMINATORY_CAP)
    return round(score, 1)


def output_schema(criteria: list[Criterion], ids: list[str]) -> dict[str, Any]:
    score = {"type": "integer", "minimum": 0, "maximum": 10}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["analyses", "synthesis"],
        "properties": {
            "analyses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "scores", "eliminatory", "summary", "red_flags", "questions"],
                    "properties": {
                        "id": {"type": "string", "enum": ids},
                        "scores": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [c.id for c in criteria],
                            "properties": {c.id: score for c in criteria},
                        },
                        "eliminatory": {"type": "boolean"},
                        "summary": {"type": "string"},
                        "red_flags": {"type": "array", "items": {"type": "string"}},
                        "questions": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "synthesis": {"type": "string"},
        },
    }


# ---------------------------------------------------------------- prompt

def listing_payload(rec: dict[str, Any], home: tuple[float, float] | None) -> dict[str, Any]:
    attrs = {k: v for k, v in (rec.get("attributes") or {}).items()
             if not any(n in k for n in _NOISE_ATTRIBUTES)}
    desc = rec.get("description") or ""
    if len(desc) > DESCRIPTION_MAX:
        desc = desc[:DESCRIPTION_MAX] + " […]"
    prices = [p for _, p in rec.get("price_history", [])]
    return {
        "id": rec["id"],
        "title": rec.get("title"),
        "price": rec.get("price"),
        "price_history": prices if len(prices) > 1 else None,
        "location": rec.get("location"),
        "distance_km": distance_km(home, rec),
        "published": rec.get("published"),
        "url": rec.get("url"),
        "attributes": attrs,
        "description": desc,
    }


def best_so_far(state: dict[str, Any], n: int = 5) -> list[dict[str, Any]]:
    analyzed = [r for r in state["listings"].values()
                if r["status"] == "analyzed" and (r.get("analysis") or {}).get("score") is not None]
    analyzed.sort(key=lambda r: r["analysis"]["score"], reverse=True)
    return [{"id": r["id"], "title": r.get("title"), "price": r.get("price"),
             "score": r["analysis"]["score"], "summary": r["analysis"].get("summary")}
            for r in analyzed[:n]]


def criteria_table(criteria: list[Criterion]) -> str:
    rows = ["| id | critère | poids | barème |", "|---|---|---|---|"]
    rows += [f"| `{c.id}` | {c.label} | {c.weight:g} | {c.description.replace(chr(10), ' ')} |"
             for c in criteria]
    return "\n".join(rows)


def build_prompt(mission: Mission, state: dict[str, Any], batch: list[dict[str, Any]]) -> str:
    home = mission.home
    parts = [
        build_context(mission),
        "---\n\n# Prompt de notation de la mission\n\n"
        + (mission.scoring_prompt.strip() or "(pas de prompt de notation : s'en tenir aux critères)"),
        "# Critères de notation\n\n" + criteria_table(mission.scoring),
        "# Meilleures annonces déjà notées\n\n```json\n"
        + json.dumps(best_so_far(state), ensure_ascii=False, indent=1) + "\n```",
        f"# Annonces à noter ({len(batch)})\n\n```json\n"
        + json.dumps([listing_payload(r, home) for r in batch], ensure_ascii=False, indent=1)
        + "\n```",
    ]
    return "\n\n".join(parts)


def system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------- inference

def run_claude(prompt: str, schema: dict[str, Any], model: str = "",
               timeout: int = CLAUDE_TIMEOUT_S) -> tuple[dict[str, Any], float | None]:
    """One headless, tool-less claude call. Returns (structured output, cost in USD)."""
    cmd = ["claude", "-p", "--output-format", "json", "--tools", "",
           "--no-session-persistence", "--strict-mcp-config", "--setting-sources", "",
           "--system-prompt", system_prompt(), "--json-schema", json.dumps(schema)]
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except FileNotFoundError as e:
        raise AnalysisError("claude CLI not found in PATH") from e
    except subprocess.TimeoutExpired as e:
        raise AnalysisError(f"claude timed out after {timeout}s") from e
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise AnalysisError(f"claude rc={proc.returncode}, non-JSON output: "
                            f"{(proc.stdout or proc.stderr)[:300]}") from e
    if out.get("is_error"):
        raise AnalysisError(f"claude error: {str(out.get('result'))[:300]}")
    data = out.get("structured_output")
    if data is None:
        try:
            data = json.loads(out.get("result") or "")
        except json.JSONDecodeError as e:
            raise AnalysisError("claude returned no structured output") from e
    return data, out.get("total_cost_usd")


# ---------------------------------------------------------------- apply

def apply_results(state: dict[str, Any], mission: Mission, batch: list[dict[str, Any]],
                  data: dict[str, Any], now: str, model: str = "") -> list[str]:
    """Store valid analyses in the state; returns the ids analyzed."""
    sent = {r["id"]: r.get("price") for r in batch}
    done = []
    for a in data.get("analyses") or []:
        lid = a.get("id")
        rec = state["listings"].get(lid)
        if lid not in sent or rec is None or rec.get("price") != sent[lid]:
            continue  # unknown id, or the listing changed meanwhile: retry next run
        scores = a.get("scores") or {}
        if any(not isinstance(scores.get(c.id), int) for c in mission.scoring):
            continue
        scores = {c.id: max(0, min(10, scores[c.id])) for c in mission.scoring}
        eliminatory = bool(a.get("eliminatory"))
        rec["analysis"] = {
            "score": overall_score(scores, mission.scoring, eliminatory),
            "scores": scores,
            "eliminatory": eliminatory,
            "summary": a.get("summary", ""),
            "red_flags": a.get("red_flags") or [],
            "questions": a.get("questions") or [],
            "analyzed_at": now,
            "price_at_analysis": rec.get("price"),
            "model": model or "default",
        }
        rec["status"] = "analyzed"
        done.append(lid)
    if synthesis := (data.get("synthesis") or "").strip():
        state["synthesis"] = {"text": synthesis, "at": now}
    return done
