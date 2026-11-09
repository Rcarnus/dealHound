"""Common data model shared by all sources."""

from __future__ import annotations

import re
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
    lat: float | None = None  # coordinates of the ad, when the source gives them
    lng: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MissionError(Exception):
    """Invalid or missing mission.yaml."""


@dataclass
class Criterion:
    """A scoring criterion: how good a deal is on one axis, scored 0-10 by the analysis."""
    id: str            # short slug, key in analysis scores
    label: str         # column header in results
    weight: float      # relative weight in the overall score
    description: str   # rubric: what 0 / 5 / 10 mean

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_CRITERION_ID = re.compile(r"^[a-z][a-z0-9_]{0,30}$")


@dataclass
class Mission:
    """A search.

    Two kinds of criteria, kept apart:
    - search criteria: `filters` + `sources` (hard filters sent to the data sources);
    - scoring criteria: `scoring` (how the analysis ranks what was found), plus the full
      scoring prompt in missions/<name>/scoring.md.
    """
    name: str
    dir: Path
    description: str
    filters: dict[str, Any]              # common search filters
    sources: dict[str, dict[str, Any]]   # source name -> source-specific search filters
    scoring: list[Criterion]
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

    @property
    def scoring_prompt(self) -> str:
        """Full scoring prompt written at creation (missions/<name>/scoring.md), if any."""
        path = self.dir / "scoring.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    @property
    def home(self) -> tuple[float, float] | None:
        """Reference point for distances: the search center (the user's home by default)."""
        loc = self.filters.get("location") or {}
        if "lat" in loc and "lng" in loc:
            return float(loc["lat"]), float(loc["lng"])
        return None

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
            scoring=_load_criteria(path, raw.get("scoring") or {}),
            notify=raw.get("notify") or {},
        )


def _load_criteria(path: Path, scoring: dict[str, Any]) -> list[Criterion]:
    criteria = []
    for i, c in enumerate(scoring.get("criteria") or []):
        cid = str(c.get("id", ""))
        if not _CRITERION_ID.match(cid):
            raise MissionError(f"{path}: scoring.criteria[{i}].id {cid!r} must be a short "
                               "lowercase slug (a-z, 0-9, _)")
        try:
            weight = float(c.get("weight", 1))
        except (TypeError, ValueError) as e:
            raise MissionError(f"{path}: scoring criterion {cid}: invalid weight") from e
        if weight <= 0:
            raise MissionError(f"{path}: scoring criterion {cid}: weight must be > 0")
        criteria.append(Criterion(id=cid, label=str(c.get("label") or cid), weight=weight,
                                  description=str(c.get("description") or "").strip()))
    ids = [c.id for c in criteria]
    if len(ids) != len(set(ids)):
        raise MissionError(f"{path}: duplicate scoring criterion ids")
    return criteria
