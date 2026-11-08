"""Source registry: name used in mission.yaml -> factory."""

from __future__ import annotations

from typing import Callable

from dealhound.sources.base import Source


def _leboncoin() -> Source:
    from dealhound.sources.leboncoin import Leboncoin
    return Leboncoin()


REGISTRY: dict[str, Callable[[], Source]] = {
    "leboncoin": _leboncoin,
}
