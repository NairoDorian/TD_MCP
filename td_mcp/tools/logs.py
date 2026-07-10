"""Token-efficient logging (Embody discipline).

Every bridge tool result can piggyback a ``_logs`` field, but to respect
context budgets we only attach WARNING/ERROR entries from a bounded ring
buffer, and the caller decides the minimum level. This keeps successful runs
quiet while still surfacing the failures an agent needs to self-heal.

Run:  uv run python -m tests.test_logs
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional


_LEVELS = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}


class LogRing:
    """Bounded ring buffer of {ts, level, msg} log records."""

    def __init__(self, maxlen: int = 200):
        self._buf: Deque[Dict[str, Any]] = deque(maxlen=maxlen)

    def add(self, level: str, msg: str, ts: Optional[float] = None) -> None:
        lvl = level.upper() if level.upper() in _LEVELS else "INFO"
        self._buf.append({"ts": ts or time.time(), "level": lvl, "msg": str(msg)})

    def clear(self) -> None:
        self._buf.clear()

    def filter(self, min_level: str = "WARNING") -> List[Dict[str, Any]]:
        cutoff = _LEVELS.get(min_level.upper(), 2)
        return [r for r in self._buf if _LEVELS.get(r["level"], 1) >= cutoff]

    def __len__(self) -> int:
        return len(self._buf)


def attach_piggyback(result: Dict[str, Any], logs: LogRing,
                     min_level: str = "WARNING") -> Dict[str, Any]:
    """Attach a compact ``_logs`` field to a result dict, but ONLY when there
    are entries at/above ``min_level`` — empty success stays token-cheap."""
    entries = logs.filter(min_level)
    if entries:
        result["_logs"] = [{"level": e["level"], "msg": e["msg"]} for e in entries]
    return result
