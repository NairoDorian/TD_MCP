#!/usr/bin/env python3
"""Bridge staleness guard (quentinR2 generated-bridge + CI guard idea).

The in-TD bridge (`bridge/td_mcp_bridge.py`) must always expose the same tool
surface the live MCP server advertises. This script fails CI if the bridge's
``_DISPATCH`` keys drift from `server_live.TOOL_REGISTRY` — the exact failure
mode the parity tests also cover, but as a fast standalone gate that doesn't
require pytest to import the TD-side module.

Run:
    uv run python scripts/check_bridge_stale.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "bridge" / "td_mcp_bridge.py"


def bridge_dispatch_keys() -> set[str]:
    text = BRIDGE.read_text(encoding="utf-8")
    start = text.index("_DISPATCH = {") + len("_DISPATCH = {")
    end = text.index("\n}\n", start)
    block = text[start:end]
    return set(re.findall(r'^\s*"([a-z0-9_]+)":\s*lambda', block, re.M))


def live_registry_keys() -> set[str]:
    sys.path.insert(0, str(ROOT))
    from td_mcp.server_live import TOOL_REGISTRY
    return set(TOOL_REGISTRY)


def main() -> int:
    bridge = bridge_dispatch_keys()
    live = live_registry_keys()
    # `get_non_default_params` is a client-side convenience (no bridge handler).
    client_only = {"get_non_default_params"}
    expected_bridge = live - client_only
    if bridge != expected_bridge:
        missing = expected_bridge - bridge
        extra = bridge - expected_bridge
        print(f"[bridge-stale] drift detected:", file=sys.stderr)
        if missing:
            print(f"  missing from bridge _DISPATCH: {sorted(missing)}", file=sys.stderr)
        if extra:
            print(f"  extra in bridge _DISPATCH:      {sorted(extra)}", file=sys.stderr)
        return 1
    print(f"[bridge-stale] ok: {len(bridge)} bridge handlers match live registry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
