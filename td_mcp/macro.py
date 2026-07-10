"""Macro recorder / replay (tdmcp macroRecorder / runMacroScript).

Records a sequence of tool calls (with their args + results) so a successful
build can be captured once and replayed deterministically — either as a `batch`
of ops for the live bridge, or re-dispatched through any callable. Pure and
serializable; no TouchDesigner required.

Run:  uv run python -m tests.test_macro
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional


class MacroRecorder:
    def __init__(self, name: str = "macro"):
        self.name = name
        self.entries: List[Dict[str, Any]] = []

    def record(self, tool: str, args: Dict[str, Any], result: Any = None,
               ok: Optional[bool] = None) -> None:
        self.entries.append({
            "tool": tool,
            "args": args or {},
            "result": result,
            "ok": ok if ok is not None else (isinstance(result, dict) and result.get("ok") is not False),
            "ts": time.time(),
        })

    def as_ops(self, only_success: bool = True) -> List[Dict[str, Any]]:
        """Flatten to {tool, args} ops suitable for the live `batch` tool."""
        ops = []
        for e in self.entries:
            if only_success and not e["ok"]:
                continue
            ops.append({"tool": e["tool"], "args": e["args"]})
        return ops

    def dedupe(self) -> int:
        """Drop repeated identical (tool,args) entries, keeping the last.
        Returns the number of entries removed."""
        seen = {}
        for e in self.entries:
            seen[(e["tool"], json.dumps(e["args"], sort_keys=True))] = e
        before = len(self.entries)
        self.entries = list(seen.values())
        return before - len(self.entries)

    def serialize(self) -> str:
        return json.dumps({"name": self.name, "entries": self.entries}, default=str)

    @classmethod
    def deserialize(cls, text: str) -> "MacroRecorder":
        data = json.loads(text)
        m = cls(data.get("name", "macro"))
        m.entries = data.get("entries", [])
        return m

    def replay(self, dispatch: Callable[[str, Dict[str, Any]], Any],
               only_success: bool = True) -> List[Any]:
        """Re-dispatch every recorded op through ``dispatch(tool, args)``.
        ``dispatch`` is typically ``td_client._call`` or a batch builder."""
        out = []
        for op in self.as_ops(only_success=only_success):
            out.append(dispatch(op["tool"], op["args"]))
        return out
