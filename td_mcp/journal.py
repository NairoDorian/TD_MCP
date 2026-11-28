"""Read the bridge's call journal and turn entries back into calls.

The live bridge writes one JSON line per mutating tool call to
``<TD_MCP_HOME>/journal/calls.jsonl`` (default ``~/.td_mcp``), with the state
the call was about to change: a parameter's mode, constant, expression and
bind; flags; the DAT text (kept by hash under ``journal/blobs/``); the
wiring; the name; a deleted COMP as a ``.tox``. TouchDesigner's undo stack
forgets everything when it closes -- the journal answers "what did the agent
change yesterday, and how do I put it back".

:func:`query` lists entries; :func:`revert_plan` builds the inverse calls of
the newest changes as a ``batch`` the agent (or the user) reviews and sends.
It never sends anything itself, and it stops at the first change it cannot
invert (``execute_python``, a pulse, a method call), because what came after
may depend on it.

Pure standard library; works with TouchDesigner closed.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: Tools whose effect cannot be inverted from the journal.
IRREVERSIBLE = {"execute_python", "exec_node_method", "pulse_parameter", "annotate",
                "timeline", "clear_script_errors"}
#: UI-only tools: nothing in the project to put back.
UI_ONLY = {"open_viewer", "move_window", "show_in_editor", "screenshot"}
#: Writers whose files are new and stay (nothing in the project changed).
FILE_ONLY = {"save_tox", "snapshot_network", "export_recipe"}


def journal_dir() -> Path:
    base = os.environ.get("TD_MCP_HOME")
    return (Path(base) if base else Path.home() / ".td_mcp") / "journal"


def entries(folder: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every readable entry, oldest first (the rotated file included)."""
    folder = folder or journal_dir()
    out: List[Dict[str, Any]] = []
    for name in ("calls.1.jsonl", "calls.jsonl"):
        f = folder / name
        if not f.is_file():
            continue
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue  # a line cut by a crash mid-write
                if isinstance(rec, dict) and rec.get("tool"):
                    out.append(rec)
    return out


_RELATIVE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd])\s*$")


def _since(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _RELATIVE.match(str(value))
    if m:
        scale = {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
        return time.time() - float(m.group(1)) * scale
    try:
        return float(value)
    except ValueError:
        raise ValueError("since: a number of seconds since the epoch, or '30m', '2h', '1d'") from None


def _paths(entry: Dict[str, Any]) -> List[str]:
    a = entry.get("args") or {}
    out = [a.get(k) for k in ("path", "to_path", "from_path", "target_parent", "new_parent")]
    out += entry.get("created") or []
    res = entry.get("result") or {}
    out += [res.get("path"), res.get("new_path")]
    return [p for p in out if isinstance(p, str) and p]


def _touches(entry: Dict[str, Any], path: str) -> bool:
    base = "/" + path.strip("/")
    return any(p == base or p.startswith(base + "/") for p in _paths(entry))


def query(limit: int = 20, changes_only: bool = True, tool: Optional[str] = None,
          path: Optional[str] = None, since: Any = None, failures: bool = False,
          session: Optional[str] = None, folder: Optional[Path] = None) -> Dict[str, Any]:
    """The newest matching entries, newest first."""
    try:
        t0 = _since(since)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    all_entries = entries(folder)
    picked = []
    for e in reversed(all_entries):
        if changes_only and e.get("risk") == "READ_ONLY":
            continue
        if tool and e.get("tool") != tool:
            continue
        if path and not _touches(e, path):
            continue
        if t0 is not None and float(e.get("ts", 0)) < t0:
            continue
        if failures and e.get("ok") is not False:
            continue
        if session and e.get("boot") != session:
            continue
        picked.append(e)
        if len(picked) >= max(1, int(limit)):
            break
    out: Dict[str, Any] = {"ok": True, "journal": str(journal_dir() if folder is None else folder),
                           "total": len(all_entries), "count": len(picked), "entries": picked}
    if not all_entries:
        out["note"] = ("the journal is empty: the bridge records changes from version 1.18 "
                       "on, unless TD_MCP_JOURNAL=0 in TouchDesigner's environment")
    return out


# --------------------------------------------------------------------------
# Inverse calls
# --------------------------------------------------------------------------
def _restore_value(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    mode = str(state.get("mode", "CONSTANT")).upper()
    if mode == "EXPRESSION" and state.get("expr"):
        return {"mode": "expression", "expr": state["expr"]}
    if mode == "BIND" and state.get("bind"):
        return {"bind": state["bind"]}
    if mode == "CONSTANT" and "val" in state:
        return {"mode": "constant", "val": state["val"]}
    return None


def _blob_path(name: Optional[str], folder: Optional[Path]) -> Optional[Path]:
    if not name:
        return None
    p = (folder or journal_dir()) / "blobs" / name
    return p if p.is_file() else None


def _inverse(entry: Dict[str, Any], folder: Optional[Path]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """``(calls that undo entry, notes)``; raises KeyError for an entry that
    cannot be inverted."""
    tool = entry["tool"]
    a = entry.get("args") or {}
    before = entry.get("before") or {}
    calls: List[Dict[str, Any]] = []
    notes: List[str] = []
    if tool in ("set_parameters", "bind_parameter_expression"):
        params = {}
        for name, state in (before.get("pars") or {}).items():
            if state.get("missing"):
                notes.append("%s did not exist before the call (a new sequence block, or a name the "
                             "operator does not have): nothing to put back" % name)
                continue
            value = _restore_value(state)
            if value is None:
                notes.append("%s was in %s mode: put it back in TouchDesigner" % (name, state.get("mode")))
                continue
            params[name] = value
        if params:
            calls.append({"tool": "set_parameters", "args": {"path": a.get("path"), "params": params}})
        return calls, notes
    if tool == "set_flags":
        if before.get("flags"):
            calls.append({"tool": "set_flags", "args": {"path": a.get("path"), "flags": before["flags"]}})
        return calls, notes
    if tool == "set_dat_text":
        blob = _blob_path(before.get("text_blob"), folder)
        if blob is None:
            raise KeyError("the previous text was not kept (too large, or the blob is gone)")
        calls.append({"tool": "set_dat_text", "args": {"path": a.get("path"),
                                                        "text": blob.read_text(encoding="utf-8")}})
        return calls, notes
    if tool == "rename_node":
        new_path = (entry.get("result") or {}).get("new_path") or (entry.get("result") or {}).get("path")
        if not new_path and a.get("path") and a.get("new_name"):
            new_path = a["path"].rsplit("/", 1)[0] + "/" + a["new_name"]
        calls.append({"tool": "rename_node", "args": {"path": new_path, "new_name": before.get("name")}})
        notes.append("expressions that were edited to the new name stay as they are")
        return calls, notes
    if tool == "set_node_position" and "x" in before:
        calls.append({"tool": "set_node_position", "args": {"path": a.get("path"), "x": before["x"],
                                                             "y": before["y"]}})
        return calls, notes
    if tool == "set_node_color" and before.get("color"):
        r, g, b = (before["color"] + [0, 0, 0])[:3]
        calls.append({"tool": "set_node_color", "args": {"path": a.get("path"), "r": r, "g": g, "b": b}})
        return calls, notes
    if tool == "set_node_comment" and "comment" in before:
        calls.append({"tool": "set_node_comment", "args": {"path": a.get("path"),
                                                            "comment": before.get("comment") or "",
                                                            "tags": before.get("tags") or []}})
        return calls, notes
    if tool in ("connect_nodes", "disconnect_nodes"):
        inputs = before.get("inputs")
        if inputs is None:
            raise KeyError("the wiring before the call was not recorded")
        k = int(a.get("to_input") or 0)
        old = inputs[k] if k < len(inputs) else None
        if old:
            calls.append({"tool": "connect_nodes", "args": {"from_path": old, "to_path": a.get("to_path"),
                                                             "to_input": k}})
        elif tool == "connect_nodes":
            calls.append({"tool": "disconnect_nodes", "args": {"from_path": a.get("from_path"),
                                                                "to_path": a.get("to_path"), "to_input": k}})
        return calls, notes
    if entry.get("created"):
        for p in reversed(entry["created"]):
            calls.append({"tool": "delete_node", "args": {"path": p}})
        if (before or {}).get("replaced"):
            notes.append("build_network(replace=true) destroyed %s; they are not in the journal"
                         % ", ".join(before["replaced"][:5]))
        return calls, notes
    if tool == "delete_node":
        parent, name = before.get("parent"), before.get("name")
        if not parent or not name:
            raise KeyError("the deleted operator was not recorded")
        tox = _blob_path(before.get("tox_blob"), folder)
        if tox is not None:
            calls.append({"tool": "restore_network", "args": {"snapshot": str(tox), "target_parent": parent}})
            notes.append("the COMP comes back from the .tox saved when it was deleted; if %s/%s "
                         "exists by then, TouchDesigner picks another name (restore_network answers "
                         "with the path)" % (parent, name))
            return calls, notes
        if before.get("type", "").endswith("COMP"):
            raise KeyError("the deleted COMP was not saved (too large?)")
        path = "%s/%s" % (parent, name)
        calls.append({"tool": "create_node", "args": {"path": parent, "type": before.get("type"), "name": name}})
        params = {n: v for n, v in ((n, _restore_value(s)) for n, s in (before.get("pars") or {}).items())
                  if v is not None}
        if params:
            calls.append({"tool": "set_parameters", "args": {"path": path, "params": params}})
        if "x" in before:
            calls.append({"tool": "set_node_position", "args": {"path": path, "x": before["x"], "y": before["y"]}})
        text = _blob_path(before.get("text_blob"), folder)
        if text is not None:
            calls.append({"tool": "set_dat_text", "args": {"path": path, "text": text.read_text(encoding="utf-8")}})
        for i, src in enumerate(before.get("inputs") or []):
            if src:
                calls.append({"tool": "connect_nodes", "args": {"from_path": src, "to_path": path, "to_input": i}})
        notes.append("operators that the deleted one fed are not rewired; the delete reported them")
        return calls, notes
    raise KeyError("no inverse is known for %s" % tool)


def revert_plan(n: int = 1, path: Optional[str] = None, session: Optional[str] = None,
                folder: Optional[Path] = None) -> Dict[str, Any]:
    """The calls that undo the newest ``n`` changes (optionally only those
    touching ``path``), newest first, as a ``batch``. Nothing is sent.

    It works like an undo stack: each step of a plan carries ``undoes`` (the
    entry it reverts), the bridge records that, and later plans skip both the
    revert steps and the entries they undid -- so asking twice walks further
    back instead of undoing the undo.
    """
    all_entries = entries(folder)
    undone = {e["undoes"] for e in all_entries if e.get("undoes")}
    picked = []
    for e in reversed(all_entries):
        if e.get("risk") == "READ_ONLY" or e.get("ok") is False:
            continue
        if e.get("undoes") or e.get("id") in undone:
            continue
        if e.get("tool") in UI_ONLY or e.get("tool") in FILE_ONLY:
            continue
        if session and e.get("boot") != session:
            continue
        if path and not _touches(e, path):
            continue
        picked.append(e)
        if len(picked) >= max(1, int(n)):
            break
    ops: List[Dict[str, Any]] = []
    covered: List[str] = []
    notes: List[str] = []
    stopped = None
    for e in picked:
        if e["tool"] in IRREVERSIBLE:
            stopped = {"id": e.get("id"), "tool": e["tool"],
                       "why": "its effect is not recorded; the changes before it may depend on it"}
            break
        try:
            calls, more = _inverse(e, folder)
        except KeyError as exc:
            stopped = {"id": e.get("id"), "tool": e["tool"], "why": str(exc).strip("'\"")}
            break
        for c in calls:
            c["undoes"] = e.get("id")
        ops.extend(calls)
        covered.append(e.get("id"))
        notes.extend("%s: %s" % (e.get("id"), m) for m in more)
    out: Dict[str, Any] = {"ok": True, "undoes": covered, "ops": ops,
                           "send": ("review, then send `ops` as they are (each keeps its `undoes`, "
                                    "so the journal knows they are a revert) with the live `batch` "
                                    "tool; one Ctrl+Z undoes the revert -- nothing has been sent")}
    if stopped:
        out["stopped_at"] = stopped
    if notes:
        out["notes"] = notes
    if not picked:
        out["note"] = "no change in the journal matches"
    return out


def _main(argv: Iterable[str]) -> int:
    args = list(argv)
    if args and args[0] == "revert":
        print(json.dumps(revert_plan(int(args[1]) if len(args) > 1 else 1), indent=1))
    else:
        print(json.dumps(query(int(args[0]) if args else 20), indent=1, default=str))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
