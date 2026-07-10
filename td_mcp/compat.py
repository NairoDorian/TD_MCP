"""Version compatibility + connection-error cache (8beeeaaat).

Two small robustness ideas from the TypeScript ``touchdesigner_mcp`` bridge:

1. **Semantic version compat** — compare the bridge's TD/build version against
   the client's expected version: MAJOR mismatch = error, MINOR = warning,
   PATCH = tolerated. Surfaces a concrete, actionable verdict instead of a raw
   version string.
2. **Connection-error cache (TTL)** — cache a transport failure (e.g. 60s) so a
   flapping bridge doesn't spam the agent with identical ECONNREFUSED noise;
   after the TTL it retries.

Run:  uv run python -m tests.test_compat
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple


def parse_version(v: str) -> Tuple[int, int, int]:
    """Parse '2023.10000' or '1.2.3' -> (major, minor, patch). Non-numeric -> 0."""
    parts = []
    for p in str(v).replace("v", "").split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def check_compat(client_ver: str, bridge_ver: str) -> Dict[str, Any]:
    """MAJOR mismatch => error, MINOR => warning, PATCH => ok."""
    a = parse_version(client_ver)
    b = parse_version(bridge_ver)
    if a[0] != b[0]:
        return {"level": "error", "compatible": False,
                "message": f"MAJOR version mismatch: client expects {client_ver}, "
                           f"bridge reports {bridge_ver}. Protocol may be incompatible."}
    if a[1] != b[1]:
        return {"level": "warning", "compatible": True,
                "message": f"MINOR version drift: client {client_ver} vs bridge "
                           f"{bridge_ver}. Some newer features may be unavailable."}
    return {"level": "ok", "compatible": True,
            "message": f"Versions aligned ({client_ver} ~ {bridge_ver})."}


class ErrorCache:
    """TTL cache for transport errors keyed by host:port."""

    def __init__(self, ttl: float = 60.0):
        self.ttl = ttl
        self._store: Dict[str, Tuple[float, str]] = {}

    def get(self, key: str) -> Optional[str]:
        item = self._store.get(key)
        if not item:
            return None
        ts, msg = item
        if time.time() - ts > self.ttl:
            del self._store[key]
            return None
        return msg

    def set(self, key: str, msg: str) -> None:
        self._store[key] = (time.time(), msg)

    def cached(self, key: str) -> bool:
        return self.get(key) is not None
