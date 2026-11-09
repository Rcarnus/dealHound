"""Single process lock shared by collect.py and analyze.py (both write state.json)."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = ROOT / ".dealhound.lock"


@contextmanager
def state_lock(blocking: bool) -> Iterator[bool]:
    """Yields True if the lock was acquired. Non-blocking: yields False when busy."""
    with open(LOCK_PATH, "w") as f:
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(f, flags)
        except BlockingIOError:
            yield False
            return
        yield True
