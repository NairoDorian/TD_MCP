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

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "bridge" / "td_mcp_bridge.py"
PYPROJECT = ROOT / "pyproject.toml"


def bridge_dispatch_keys() -> set[str]:
    text = BRIDGE.read_text(encoding="utf-8")
    start = text.index("_DISPATCH = {") + len("_DISPATCH = {")
    end = text.index("\n}\n", start)
    block = text[start:end]
    return set(re.findall(r'^\s*"([a-z0-9_]+)":\s*lambda', block, re.M))


def bridge_version() -> str | None:
    m = re.search(r'^BRIDGE_VERSION = "([^"]+)"', BRIDGE.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def bridge_td_mcp_imports() -> list[str]:
    """Imports of `td_mcp` under bridge/.

    Forbidden outright in the bridge and the builder; tolerated elsewhere only
    inside a try/except that carries a fallback. The bridge runs inside
    TouchDesigner as a Text DAT, where td_mcp is not on sys.path.
    `from td_mcp.streamable_http import StreamableHTTPMixin` sat at the top of
    td_mcp_bridge.py inside a try/except that set a flag -- and the handler
    class then inherited from the missing name regardless. The bridge had
    never compiled in TouchDesigner; pytest never noticed because it runs with
    td_mcp importable.
    """
    strict = {"td_mcp_bridge.py", "bootstrap.py"}
    out: list[str] = []
    for path in sorted((ROOT / "bridge").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        guarded: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for stmt in node.body:
                    for inner in ast.walk(stmt):
                        guarded.add(id(inner))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            hits = [n for n in names if n.split(".")[0] == "td_mcp"]
            if not hits:
                continue
            if path.name in strict or id(node) not in guarded:
                out.append(f"{path.name}:{node.lineno}: imports {', '.join(hits)}")
    return out


def forked_bridge_copies() -> list[tuple[str, str]]:
    """Files under bridge/ that have started carrying bridge logic of their own.

    `bootstrap.py` used to be a full second copy of the bridge. It drifted to
    39 tools against the bridge's 46 and carried three reversed `COMP.create`
    calls, and nothing noticed because this guard only ever compared
    `td_mcp_bridge.py` to the live registry. A tool table or a `_do_*` handler
    anywhere other than the bridge itself means the fork is back.
    """
    out: list[tuple[str, str]] = []
    for path in sorted((ROOT / "bridge").glob("*.py")):
        if path.name == BRIDGE.name:
            continue
        text = path.read_text(encoding="utf-8")
        if "_DISPATCH = {" in text:
            out.append((path.name, "declares its own _DISPATCH table"))
        elif re.search(r"^def _do_", text, re.M):
            out.append((path.name, "declares its own _do_* handlers"))
    return out


def package_version() -> str | None:
    m = re.search(r'^version = "([^"]+)"', PYPROJECT.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def live_registry_keys() -> set[str]:
    sys.path.insert(0, str(ROOT))
    from td_mcp.server_live import TOOL_REGISTRY
    return set(TOOL_REGISTRY)


def main() -> int:
    # Version parity: the bridge stamps BRIDGE_VERSION so the live server can
    # tell a stale in-TD paste apart from a genuine unknown tool. That signal
    # is only worth anything if the stamp tracks the package version.
    bv, pv = bridge_version(), package_version()
    if bv is None:
        print("[bridge-stale] BRIDGE_VERSION not found in bridge", file=sys.stderr)
        return 1
    if bv != pv:
        print(f"[bridge-stale] version drift: BRIDGE_VERSION={bv} but "
              f"pyproject version={pv}", file=sys.stderr)
        return 1

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
    td_mcp_imports = bridge_td_mcp_imports()
    if td_mcp_imports:
        print("[bridge-stale] bridge code imports td_mcp, which is not importable "
              "inside TouchDesigner (the bridge runs in a Text DAT):",
              file=sys.stderr)
        for entry in td_mcp_imports:
            print(f"  {entry}", file=sys.stderr)
        return 1

    forked = forked_bridge_copies()
    if forked:
        print("[bridge-stale] a second copy of the bridge exists:", file=sys.stderr)
        for path, why in forked:
            print(f"  {path}: {why}", file=sys.stderr)
        print("  The bridge must have exactly one implementation. bootstrap.py "
              "held a fork until v1.12.0 and it drifted to 39 tools with three "
              "reversed create() calls; nothing compared the two copies.",
              file=sys.stderr)
        return 1

    print(f"[bridge-stale] ok: {len(bridge)} bridge handlers match live registry "
          f"(version {bv}); no forked copies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
