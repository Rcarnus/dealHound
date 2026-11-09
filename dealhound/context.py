"""Assemble the context given to the analysis (claude -p).

Order: general user context (context/*.md, README excluded), then the mission
(description, search criteria, missions/<name>/context.md). The scoring prompt and
criteria are added by dealhound.analysis.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from dealhound.models import Mission

ROOT = Path(__file__).resolve().parent.parent
CONTEXT_DIR = ROOT / "context"


def user_context(context_dir: Path = CONTEXT_DIR) -> str:
    parts = []
    for path in sorted(context_dir.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        parts.append(f"<!-- context/{path.name} -->\n{path.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(parts)


def mission_context(mission: Mission) -> str:
    parts = [f"# Mission : {mission.name}", mission.description]
    search = {"filters": mission.filters, "sources": mission.sources}
    parts.append("## Critères de recherche (filtres déjà appliqués par les sources)\n```yaml\n"
                 + yaml.safe_dump(search, allow_unicode=True, sort_keys=False).strip()
                 + "\n```")
    if extra := mission.context.strip():
        parts.append(f"<!-- missions/{mission.dir.name}/context.md -->\n{extra}")
    return "\n\n".join(p for p in parts if p)


def build_context(mission: Mission, context_dir: Path = CONTEXT_DIR) -> str:
    return ("# Contexte utilisateur (général)\n\n" + user_context(context_dir)
            + "\n\n---\n\n" + mission_context(mission))
