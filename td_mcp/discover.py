"""Multi-instance TD discovery (twozero_td_mcp).

twozero runs inside the TD plugin and lets the agent pick among several open
TD instances. This module reproduces the *discovery* half as a pure function:
given a set of candidate hosts/ports it probes each (via an injectable probe,
so it is fully testable without a live TD) and returns the reachable bridge
instances the agent can choose from.

Run:  uv run python -m tests.test_discover
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

# Ports used across the TD MCP ecosystem (Embody 9870, johnsabath 9988,
# pantani/touch-mcp 9980, superdwayne 8053, twozero 40404, td-mcp http 8765).
KNOWN_PORTS = [9980, 9988, 9870, 8053, 40404, 8765]
DEFAULT_HOSTS = ["127.0.0.1", "localhost"]


def _default_probe(host: str, port: int, timeout: float) -> Optional[Dict]:
    """No-op probe (no network). Override with a real HTTP health probe."""
    return None


def discover_instances(hosts: Optional[List[str]] = None,
                       ports: Optional[List[int]] = None,
                       probe: Callable[[str, int, float], Optional[Dict]] = _default_probe,
                       timeout: float = 0.2) -> List[Dict]:
    """Return reachable bridge instances.

    ``probe(host, port, timeout) -> dict | None`` — return instance metadata
    (e.g. ``{"name": ..., "version": ...}``) or ``None`` if unreachable. Swap in
    a real ``urllib``/``requests`` health check for production use.
    """
    hosts = hosts or list(DEFAULT_HOSTS)
    ports = ports or sorted(set(KNOWN_PORTS))
    found: List[Dict] = []
    for host in hosts:
        for port in ports:
            info = probe(host, port, timeout)
            if info is not None:
                found.append({"host": host, "port": port, "info": info})
    return found


def pick_instance(instances: List[Dict], prefer_port: Optional[int] = None) -> Optional[Dict]:
    """Choose a target instance: prefer ``prefer_port`` if present, else the
    first discovered (stable ordering)."""
    if not instances:
        return None
    if prefer_port is not None:
        for inst in instances:
            if inst["port"] == prefer_port:
                return inst
    return instances[0]
