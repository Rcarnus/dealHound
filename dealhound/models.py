"""Common data model shared by all sources."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Listing:
    id: str  # "<source>:<source_id>"
    source: str
    url: str
    title: str
    price: float | None
    location: str = ""
    published: str = ""  # date string as given by the source
    description: str = ""
    attributes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MissionError(Exception):
    """Invalid or missing mission.yaml."""


@dataclass
class Mission:
    name: str
    dir: Path
    description: str
    filters: dict[str, Any]              # common hard filters
    sources: dict[str, dict[str, Any]]   # source name -> source-specific filter overrides
    analysis: dict[str, Any]
    notify: dict[str, Any]

    @property
    def state_path(self) -> Path:
        return self.dir / "state.json"

    @property
    def deals_path(self) -> Path:
        return self.dir / "deals.md"

    @property
    def context(self) -> str:
        """Free-form context for the analysis (missions/<name>/context.md), if any."""
        path = self.dir / "context.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def filters_for(self, source: str) -> dict[str, Any]:
        """Common filters merged with the source-specific overrides."""
        return {**self.filters, **(self.sources.get(source) or {})}

    @classmethod
    def load(cls, mission_dir: Path) -> "Mission":
        import yaml

        path = mission_dir / "mission.yaml"
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except FileNotFoundError as e:
            raise MissionError(f"{path} not found") from e
        except yaml.YAMLError as e:
            raise MissionError(f"{path}: invalid YAML: {e}") from e

        sources = raw.get("sources")
        if isinstance(sources, list):  # short form: sources: [leboncoin]
            sources = {s: {} for s in sources}
        if not sources:
            raise MissionError(f"{path}: 'sources' is required")

        return cls(
            name=raw.get("name") or mission_dir.name,
            dir=mission_dir,
            description=(raw.get("description") or "").strip(),
            filters=raw.get("filters") or {},
            sources=sources,
            analysis=raw.get("analysis") or {},
            notify=raw.get("notify") or {},
        )
