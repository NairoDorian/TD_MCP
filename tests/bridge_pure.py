"""Load the bridge's pure helpers straight out of the shipped source.

The in-TD bridge is a single file pasted into a Text DAT, so it cannot import
from ``td_mcp``. The alternative the older tests used -- re-typing each helper
into the test file -- means the tests drift from the bridge silently. Instead
this slices the block between the BEGIN/END PURE HELPERS markers out of
``bridge/td_mcp_bridge.py`` and execs it, so what the tests exercise is
literally what ships.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict

BRIDGE_PATH = os.path.join(os.path.dirname(__file__), "..", "bridge", "td_mcp_bridge.py")

_BEGIN = "# BEGIN PURE HELPERS"
_END = "# END PURE HELPERS"


def pure_source() -> str:
    """The bridge's pure-helper block, verbatim."""
    with open(BRIDGE_PATH, encoding="utf-8") as fh:
        text = fh.read()
    start = text.index(_BEGIN)
    end = text.index(_END)
    return text[start:end]


def load() -> Dict[str, Any]:
    """Exec the pure block and return its namespace."""
    import ast as _ast

    namespace: Dict[str, Any] = {
        "_ast": _ast,
        "re": re,
        # code_route_denied echoes the bridge's configured mode; the block
        # itself never sets it, so tests supply a stand-in.
        "EXEC_MODE": "off",
    }
    exec(compile(pure_source(), BRIDGE_PATH, "exec"), namespace)  # noqa: S102
    return namespace
