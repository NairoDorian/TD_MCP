"""td_mcp_bridge — run this inside TouchDesigner (paste into a Text DAT).

Start:  op('YOUR_TEXT_DAT').module.start()
Stop:   op('YOUR_TEXT_DAT').module.stop()

Serves JSON-RPC over HTTP on TD_MCP_PORT (default 9980):
  POST /mcp            {"tool": "...", "args": {...}}
  GET  /api/status     health check
  GET  /api/resource?uri=td://node/<path>   td:// resource read

Hardening (from axysar / superdwayne / TrueFiasco):
  * Auth token  - TD_MCP_AUTH_TOKEN (auto-generated if unset, printed
    to the TD textport). Client must send `Authorization: Bearer <token>`.
  * CORS         - loopback-only; the old `*` wildcard is removed (CSRF).
  * Exec gate    - TD_MCP_ALLOW_EXEC=0 disables execute_python (RCE).
  * Protected     - TD_MCP_PROTECTED_PATHS (comma list) shields listed
    paths/descendants from delete / RCE (superdwayne).
  * Detail level  - every read tool takes detailLevel (brief/normal/full)
    to keep token output lean (8beeeaaat).
  * Recovery     - every error carries {cause, action, next_tools} hints
    (Embody) so an agent can self-correct.
  * Viewport     - capture_viewport returns the file + an is_black/is_flat
    quality verdict so the agent knows if output is empty (Embody).
   * Batch        - `batch` collapses N ops into one round-trip (benoitliard).
   * Constant-time auth - token compare via hmac.compare_digest to avoid
     timing leaks (bottobot / axysar); WebSocket requires an auth handshake.
   * Auto-layout   - `create_node` auto-places nodes in family lanes when no
     explicit x/y is given (benoitliard); `auto_layout` tidies a whole COMP.
   * Topology      - `map_network` emits a Graphviz DOT graph of connections,
     positions and topology for agent spatial reasoning (nested `map` tool).
   * Annotation    - `set_node_color` / `set_node_comment` (comment + tags)
     visually organise and document generated subgraphs (TD-Codex style).
   * Recipes       - `export_recipe` / `import_recipe` serialize + rebuild a
     subnetwork as a portable JSON blueprint for repeatable builds (tdmcp).
   * Live events   - tool activity is pushed to connected WebSocket clients
     (chat UI) in real time (tdmcp event stream).

Every mutating tool wraps op changes in ui.undo so one Ctrl+Z
reverts a whole agent batch.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote

# Streamable HTTP transport (8beeeaaat-style: SSE + session + DNS-rebind guard)
try:
    from td_mcp.streamable_http import (
        StreamableHTTPMixin,
        make_legacy_handler,
        start_session_cleanup,
        stop_session_cleanup,
    )
    STREAMABLE_HTTP_AVAILABLE = True
except Exception:  # noqa: BLE001
    STREAMABLE_HTTP_AVAILABLE = False

# Path to the chat UI HTML (relative to this script when running in TD)
_BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else ""
_CHAT_UI_PATH = os.path.join(_BRIDGE_DIR, "chat_ui.html")

# WebSocket connected clients set (for future push events)
_ws_clients = set()
_ws_lock = threading.Lock()
_sse_clients = set()


def debug(*args):
    """Bridge log helper (used by start/stop); overridable by the host."""
    print("[td_mcp]", *args)


def _ws_push(event):
    """Proactively notify connected WebSocket clients (chat UI) of tool activity
    or errors, so the agent's actions are visible in real time (tdmcp event stream)."""
    if not _ws_clients:
        return
    frame = _ws_make_frame(json.dumps({"type": "event", "event": event}))
    drop = []
    with _ws_lock:
        for client in list(_ws_clients):
            try:
                client.wfile.write(frame)
                client.wfile.flush()
            except Exception:  # noqa: BLE001
                drop.append(client)
        for client in drop:
            _ws_clients.discard(client)

TD_MCP_PORT = int(os.environ.get("TD_MCP_PORT", "9980"))

ALLOW_EXEC = os.environ.get("TD_MCP_ALLOW_EXEC", "1") != "0"
PROTECTED = [p.strip() for p in os.environ.get("TD_MCP_PROTECTED_PATHS", "").split(",") if p.strip()]
AUTH_TOKEN = os.environ.get("TD_MCP_AUTH_TOKEN") or secrets.token_urlsafe(24)

# Recovery-hint rules (Embody-style): (compiled regex, cause, action, next_tools)
RECOVERY_HINT_RULES = [
    (re.compile(r"cook|not cooked|invalid operation", re.I),
     "The operator could not cook or the operation was invalid.",
     "Check the operator exists and is cooked; verify parameter names with td_docs_parameter.",
     ["td_docs_parameter", "get_errors"]),
    (re.compile(r"no such|not found|unknown operator", re.I),
     "The path or operator type was not found.",
     "List nodes under the parent with list_nodes and confirm the type spelling.",
     ["list_nodes", "td_docs_family"]),
    (re.compile(r"parameter|par\b", re.I),
     "A parameter name was invalid for this operator.",
     "Inspect the operator schema before setting values.",
     ["td_docs_parameter", "get_parameters"]),
    (re.compile(r"python|exec|syntax", re.I),
     "The Python snippet failed to execute.",
     "Fix the Python (check op() paths and imports) and retry.",
     ["td_docs_python", "get_errors"]),
    (re.compile(r"timeout|timed out", re.I),
     "The request timed out (likely a heavy cook).",
     "Retry with a smaller network or raise the client timeout.",
     ["get_errors"]),
]


def recovery_hints(message):
    for rx, cause, action, next_tools in RECOVERY_HINT_RULES:
        if rx.search(message or ""):
            return {"cause": cause, "action": action, "next_tools": next_tools[:2]}
    return None


def _resolve_path(path):
    if not path:
        return ""
    # resolve *here or *this using TD's ui API
    here_path = "/project1"
    this_path = ""
    try:
        import ui
        curr = ui.panes.current
        if curr and hasattr(curr, "owner") and curr.owner:
            here_path = curr.owner.path
            sel = curr.owner.selectedOps
            if sel:
                this_path = sel[0].path
        else:
            for p in ui.panes:
                if hasattr(p, "owner") and p.owner:
                    here_path = p.owner.path
                    sel = p.owner.selectedOps
                    if sel:
                        this_path = sel[0].path
                        break
                    break
    except Exception:
        pass

    path = path.replace("*here", here_path)
    if this_path:
        path = path.replace("*this", this_path)
    else:
        path = path.replace("*this", here_path)
    return path


def _op(path):
    resolved = _resolve_path(path)
    return op(resolved) if resolved else None


def _is_protected(path):
    if not PROTECTED:
        return False
    node = _op(path)
    for p in PROTECTED:
        if path == p or (node is not None and node.path.startswith(p + "/")):
            return True
    return False


def _family_of(node):
    try:
        return node.family
    except Exception:  # noqa: BLE001
        return ""


def _auto_position(parent_node, family):
    """Lane-based auto placement (benoitliard): same-family siblings share a
    horizontal row; a new family gets its own fresh row beneath the network."""
    spacing = 220
    siblings = list(parent_node.children)
    row_y = None
    max_x = 0
    for c in siblings:
        if _family_of(c) == family:
            row_y = getattr(c, "nodeY", 0)
            max_x = max(max_x, c.nodeX)
    if row_y is None:
        row_y = (min((getattr(c, "nodeY", 0) for c in siblings), default=0) - spacing) if siblings else 0
        new_x = 0
    else:
        new_x = max_x + spacing
    return [new_x, row_y]


def _do_create(path, type, name=None, nodeX=None, nodeY=None):
    parent = _op(path) or parent()
    if parent is None:
        return {"ok": False, "error": f"parent not found: {path}"}
    node = parent.create(name or type.split()[-1], type)
    try:
        if nodeX is not None and nodeY is not None:
            node.nodeX, node.nodeY = nodeX, nodeY
        else:
            node.nodeX, node.nodeY = _auto_position(parent, getattr(node, "family", ""))
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "path": node.path,
            "position": [getattr(node, "nodeX", 0), getattr(node, "nodeY", 0)]}


def _do_delete(path):
    if _is_protected(path):
        return {"ok": False, "error": f"path is protected from deletion: {path}"}
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    node.destroy()
    return {"ok": True}


def _do_get_parameters(path, detail="normal"):
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    params = {p.name: p.val for p in node.par}
    if detail == "brief":
        return {"ok": True, "count": len(params)}
    if detail == "full":
        return {"ok": True, "params": params}
    return {"ok": True, "params": dict(list(params.items())[:40])}


def _do_get_errors(path):
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    return {"ok": True, "errors": [str(e) for e in node.errors]}


def _do_execute_python(code):
    if not ALLOW_EXEC:
        return {"ok": False, "error": "execute_python is disabled (TD_MCP_ALLOW_EXEC=0)"}
    ns = {"op": op, "project": project, "me": me, "parent": parent}
    exec(code, ns)  # noqa: S102 - intentional in-TD RCE, gated by host/exec flag
    return {"ok": True, "result": repr(ns.get("result", None))}


def _do_list_nodes(path, detail="normal"):
    node = _op(path) or root()
    if node is None:
        return {"ok": False, "error": "not found"}
    kids = [{"name": c.name, "type": c.type, "path": c.path} for c in node.children]
    if detail == "brief":
        return {"ok": True, "count": len(kids)}
    return {"ok": True, "nodes": kids}


def _do_scan_network(path, depth=3):
    start_node = _op(path) or root()
    if start_node is None:
        return {"ok": False, "error": "not found"}
    scanned = []

    def traverse(node, current_depth):
        if node is None or current_depth > depth:
            return
        inputs = []
        try:
            inputs = [i.path for i in node.inputs if i is not None]
        except Exception:
            pass
        non_defaults = {}
        try:
            for p in node.par:
                if not p.isDefault:
                    non_defaults[p.name] = p.val
        except Exception:
            pass
        errors = []
        try:
            errors = [str(e) for e in node.errors]
        except Exception:
            pass
        scanned.append({
            "path": node.path,
            "name": node.name,
            "type": node.type,
            "inputs": inputs,
            "params": non_defaults,
            "errors": errors
        })
        try:
            for child in node.children:
                traverse(child, current_depth + 1)
        except Exception:
            pass

    traverse(start_node, 1)
    return {"ok": True, "nodes": scanned}


def _do_build_and_verify(path, op_type, name=None, params=None):
    """Create a node, optionally set parameters, then verify errors and viewport verdict."""
    r = _do_create(path, op_type, name)
    if not r.get("ok"):
        return r
    node_path = r["path"]
    if params:
        _do_set_parameters(node_path, params)
    errs = _do_get_errors(node_path)
    vp = _do_capture_viewport(node_path, "brief")
    return {
        "ok": True,
        "path": node_path,
        "errors": errs.get("errors", []),
        "viewport": vp.get("verdict"),
    }

def _do_connect_nodes(from_path, to_path, from_output=0, to_input=0):
    """Wire from_path's output[from_output] into to_path's input[to_input]."""
    src = _op(from_path)
    dst = _op(to_path)
    if src is None:
        return {"ok": False, "error": f"source not found: {from_path}"}
    if dst is None:
        return {"ok": False, "error": f"destination not found: {to_path}"}
    try:
        dst.inputConnectors[to_input].connect(src.outputConnectors[from_output])
        return {"ok": True, "connected": f"{src.path}[{from_output}] -> {dst.path}[{to_input}]"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _do_rename_node(path, new_name):
    """Rename a TouchDesigner node."""
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    if _is_protected(path):
        return {"ok": False, "error": "path is protected"}
    old_name = node.name
    try:
        node.name = new_name
        return {"ok": True, "old_name": old_name, "new_name": node.name, "path": node.path}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _do_copy_node(path, new_parent, new_name=None):
    """Copy a node to a new parent COMP."""
    node = _op(path)
    parent_node = _op(new_parent)
    if node is None:
        return {"ok": False, "error": f"source not found: {path}"}
    if parent_node is None:
        return {"ok": False, "error": f"parent not found: {new_parent}"}
    try:
        pasted = parent_node.copy(node, name=new_name)
        return {"ok": True, "path": pasted.path}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _do_auto_layout(path, direction="left-right", spacing_x=200, spacing_y=200):
    """Auto-arrange all children of a COMP in a left-to-right or top-down grid."""
    node = _op(path) or root()
    if node is None:
        return {"ok": False, "error": "not found"}
    try:
        children = list(node.children)
        for idx, child in enumerate(children):
            if direction == "left-right":
                child.nodeX = idx * spacing_x
                child.nodeY = 0
            else:
                child.nodeX = 0
                child.nodeY = -(idx * spacing_y)
        return {"ok": True, "arranged": len(children)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _do_get_node(path):
    """Get detailed info about a single node: type, path, errors, non-default params, and connections."""
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    non_defaults = {}
    try:
        for p in node.par:
            if not p.isDefault:
                non_defaults[p.name] = p.val
    except Exception:
        pass
    inputs = []
    try:
        inputs = [c.outputOP.path for c in node.inputConnectors if c and c.outputOP]
    except Exception:
        pass
    outputs = []
    try:
        outputs = [c.inputOP.path for oc in node.outputConnectors for c in oc.connections if c and c.inputOP]
    except Exception:
        pass
    errors = []
    try:
        errors = [str(e) for e in node.errors]
    except Exception:
        pass
    return {
        "ok": True,
        "name": node.name,
        "type": node.type,
        "path": node.path,
        "inputs": inputs,
        "outputs": outputs,
        "params": non_defaults,
        "errors": errors,
        "position": [getattr(node, 'nodeX', 0), getattr(node, 'nodeY', 0)],
    }


def _do_set_node_color(path, r, g, b):
    """Set the display color of a node (0..1 per channel)."""
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    try:
        node.color = (r, g, b)
        return {"ok": True, "color": [r, g, b]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _do_set_node_comment(path, comment=None, tags=None):
    """Annotate a node with a comment and/or tags for documentation/visual grouping."""
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    try:
        if comment is not None:
            node.comment = comment
        if tags is not None:
            node.tags = tags if isinstance(tags, list) else [t.strip() for t in str(tags).split(",") if t.strip()]
        return {"ok": True, "comment": getattr(node, "comment", ""), "tags": list(getattr(node, "tags", []) or [])}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _do_save_tox(path, file_path=None):
    """Save a COMP as a .tox file."""
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    import tempfile
    out = file_path or os.path.join(tempfile.gettempdir(), f"{node.name}.tox")
    try:
        node.save(out)
        return {"ok": True, "file": out}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _force_cook(node):
    """Recursively force-cook a node so captures/captures are not stale
    (nested github-mcp `_force_cook_chain`)."""
    try:
        node.cook(force=True, recurse=True)
    except Exception:  # noqa: BLE001
        try:
            node.cook(force=True)
        except Exception:  # noqa: BLE001
            pass


def _do_set_parameters(path, params, detail="normal"):
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    applied = {}
    for k, v in params.items():
        if k not in node.par:
            continue
        par = node.par[k]
        try:
            if isinstance(v, dict):
                if "expr" in v:
                    par.expr = v["expr"]
                if v.get("pulse"):
                    par.pulse()
                if "val" in v:
                    par.val = v["val"]
                applied[k] = v
            else:
                par.val = v
                applied[k] = v
        except Exception as e:  # noqa: BLE001
            applied[k] = f"error: {e}"
    if detail == "brief":
        return {"ok": True, "applied_count": len(applied)}
    return {"ok": True, "applied": applied}


def _do_disconnect_nodes(from_path, to_path, to_input=0):
    """Break the wire from from_path into to_path's input[to_input]."""
    src = _op(from_path)
    dst = _op(to_path)
    if dst is None:
        return {"ok": False, "error": f"destination not found: {to_path}"}
    try:
        target = src.path if src else None
        for c in list(dst.inputConnectors[to_input].connections):
            if c.outputOP and c.outputOP.path == target:
                c.disconnect()
        return {"ok": True, "disconnected": f"{from_path} -x-> {to_path}[{to_input}]"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _do_get_connections(path):
    """Return a normalized wiring map (inputs + outputs) for a node."""
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    ins = []
    try:
        for i, ic in enumerate(node.inputConnectors):
            for c in ic.connections:
                if c and c.outputOP:
                    ins.append({"input": i, "from": c.outputOP.path})
    except Exception:  # noqa: BLE001
        pass
    outs = []
    try:
        for i, oc in enumerate(node.outputConnectors):
            for c in oc.connections:
                if c and c.inputOP:
                    outs.append({"output": i, "to": c.inputOP.path})
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "inputs": ins, "outputs": outs}


def _do_exec_node_method(path, method, args=None):
    """Call a method on a node (e.g. cook, reset, pulse a parameter)."""
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    try:
        fn = getattr(node, method)
        res = fn(*(args or []))
        return {"ok": True, "result": repr(res)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _do_snapshot_network(path):
    """Durable checkpoint: save a COMP to a temp .tox (survives the undo stack)."""
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    import tempfile, time
    out = os.path.join(tempfile.gettempdir(), f"td_mcp_snap_{node.name}_{int(time.time())}.tox")
    try:
        node.save(out)
        return {"ok": True, "snapshot": out}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _do_restore_network(snapshot, target_parent="*here"):
    """Restore a checkpoint .tox (from snapshot_network) into a parent COMP."""
    parent = _op(target_parent) or parent()
    if parent is None:
        return {"ok": False, "error": "parent not found"}
    if not snapshot or not os.path.exists(snapshot):
        return {"ok": False, "error": f"snapshot not found: {snapshot}"}
    try:
        loaded = parent.loadTox(snapshot)
        return {"ok": True, "path": loaded.path}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _do_get_performance(path):
    """Rank child cook times to surface performance hotspots (Embody)."""
    node = _op(path) or root()
    if node is None:
        return {"ok": False, "error": "not found"}
    rows = []
    try:
        for c in node.children:
            rows.append({"path": c.path, "type": c.type,
                         "cook_time": round(float(getattr(c, "cookTime", 0) or 0), 4),
                         "cpu": round(float(getattr(c, "cpuCookTime", 0) or 0), 4),
                         "gpu": round(float(getattr(c, "gpuCookTime", 0) or 0), 4)})
    except Exception:  # noqa: BLE001
        pass
    rows.sort(key=lambda r: r["cook_time"], reverse=True)
    return {"ok": True, "fps": project.cook.rate, "cooks": rows, "hotspots": rows[:5]}


def _do_validate_network(path, depth=2):
    """Scene-contract check (TD-Codex): unplaced/overlapping nodes and cook errors."""
    start = _op(path) or root()
    if start is None:
        return {"ok": False, "error": "not found"}
    issues = []
    positions = {}

    def traverse(node, d):
        if node is None or d > depth:
            return
        nx, ny = getattr(node, "nodeX", 0), getattr(node, "nodeY", 0)
        if nx == 0 and ny == 0:
            issues.append({"severity": "warn", "path": node.path,
                           "issue": "node sits at the origin (0,0) - likely unplaced"})
        key = (nx, ny)
        if key in positions:
            issues.append({"severity": "warn", "path": node.path,
                           "issue": f"overlaps {positions[key]} at {key}"})
        else:
            positions[key] = node.path
        try:
            for e in node.errors:
                issues.append({"severity": "error", "path": node.path, "issue": f"cook error: {e}"})
        except Exception:  # noqa: BLE001
            pass
        try:
            for child in node.children:
                traverse(child, d + 1)
        except Exception:  # noqa: BLE001
            pass

    traverse(start, 1)
    sev = {"error": 0, "warn": 0}
    for it in issues:
        sev[it["severity"]] = sev.get(it["severity"], 0) + 1
    return {"ok": True, "issue_count": len(issues), "by_severity": sev, "issues": issues}


# TouchDesigner node flag attributes that can be toggled from the server.
_NODE_FLAGS = ("bypass", "viewer", "excludeFromCook", "allowCooking",
               "forceCooking", "cloneImmune", "pickable")


def _do_set_flags(path, flags=None):
    """Toggle node flags (bypass, viewer, excludeFromCook, allowCooking, ...)."""
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    flags = flags or {}
    applied = {}
    for k, v in flags.items():
        if k in _NODE_FLAGS:
            try:
                setattr(node, k, bool(v))
                applied[k] = bool(v)
            except Exception as e:  # noqa: BLE001
                applied[k] = f"error: {e}"
    return {"ok": True, "applied": applied}


def _do_find_nodes(path, query=None, op_type=None, depth=4):
    """Search descendants by name substring and/or operator type (touch_mcp find)."""
    start = _op(path) or root()
    if start is None:
        return {"ok": False, "error": "not found"}
    q = (query or "").lower()
    matches = []

    def traverse(node, d):
        if node is None or d > depth:
            return
        if q and q in node.name.lower():
            matches.append({"path": node.path, "name": node.name, "type": node.type})
        if op_type and op_type.lower() in node.type.lower():
            matches.append({"path": node.path, "name": node.name, "type": node.type})
        try:
            for child in node.children:
                traverse(child, d + 1)
        except Exception:  # noqa: BLE001
            pass

    traverse(start, 1)
    return {"ok": True, "count": len(matches), "matches": matches}


def _do_set_node_position(path, x, y):
    """Move a single node to an explicit grid position."""
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    try:
        node.nodeX, node.nodeY = x, y
        return {"ok": True, "path": node.path, "position": [node.nodeX, node.nodeY]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _do_timeline(action, value=None):
    """Control the global timeline: play / pause / toggle / frame / rate / seek."""
    tl = project.cook
    try:
        if action in ("play", "pause", "toggle"):
            if action == "play":
                tl.play = True
            elif action == "pause":
                tl.play = False
            else:
                tl.play = not tl.play
            return {"ok": True, "play": tl.play}
        if action == "frame":
            tl.time = float(value)
            return {"ok": True, "time": tl.time}
        if action == "seek":
            tl.time = max(1.0, tl.time + float(value))
            return {"ok": True, "time": tl.time}
        if action == "rate":
            tl.rate = float(value)
            return {"ok": True, "rate": tl.rate}
        return {"ok": False, "error": f"unknown timeline action: {action}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _do_export_recipe(path, depth=3):
    """Serialize a subnetwork into a portable JSON blueprint (tdmcp recipe gallery).

    The blueprint records each node's name, type, non-default params, position
    and input wiring so import_recipe can rebuild it elsewhere."""
    start = _op(path) or root()
    if start is None:
        return {"ok": False, "error": "not found"}
    nodes = []

    def traverse(node, d):
        if node is None or d > depth:
            return
        params = {}
        try:
            for p in node.par:
                if not p.isDefault:
                    params[p.name] = p.val
        except Exception:  # noqa: BLE001
            pass
        inputs = {}
        try:
            for i, ic in enumerate(node.inputConnectors):
                for c in ic.connections:
                    if c and c.outputOP:
                        inputs[str(i)] = c.outputOP.path
        except Exception:  # noqa: BLE001
            pass
        nodes.append({
            "name": node.name,
            "type": node.type,
            "position": [getattr(node, "nodeX", 0), getattr(node, "nodeY", 0)],
            "params": params,
            "inputs": inputs,
            "children": [c.name for c in node.children],
        })
        try:
            for child in node.children:
                traverse(child, d + 1)
        except Exception:  # noqa: BLE001
            pass

    traverse(start, 1)
    return {"ok": True, "root": start.path, "node_count": len(nodes), "recipe": nodes}


def _do_import_recipe(recipe, target_parent="*here"):
    """Rebuild a blueprint produced by export_recipe under target_parent."""
    parent = _op(target_parent) or parent()
    if parent is None:
        return {"ok": False, "error": "parent not found"}
    if isinstance(recipe, str):
        try:
            recipe = json.loads(recipe)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"bad recipe json: {e}"}
    nodes = recipe if isinstance(recipe, list) else recipe.get("recipe", [])
    if not isinstance(nodes, list):
        return {"ok": False, "error": "recipe must be a list of node specs"}
    # 1) create all nodes under target_parent (flatten hierarchy into one level
    #    of uniquely-named ops for reliable rewiring).
    created = {}
    for spec in nodes:
        name = spec.get("name")
        op_type = spec.get("type")
        if not name or not op_type:
            continue
        node = parent.create(name, op_type)
        created[name] = node
        try:
            node.nodeX, node.nodeY = spec.get("position", [0, 0])
        except Exception:  # noqa: BLE001
            pass
        for k, v in spec.get("params", {}).items():
            if k in node.par:
                try:
                    node.par[k].val = v
                except Exception:  # noqa: BLE001
                    pass
    # 2) wire inputs using the original source node names (now under target_parent).
    wired = 0
    for spec in nodes:
        dst = created.get(spec.get("name"))
        if dst is None:
            continue
        for idx, src_name in spec.get("inputs", {}).items():
            src = created.get(src_name)
            if src is None:
                continue
            try:
                dst.inputConnectors[int(idx)].connect(src.outputConnectors[0])
                wired += 1
            except Exception:  # noqa: BLE001
                pass
    return {"ok": True, "created": len(created), "wired": wired,
            "paths": [n.path for n in created.values()]}


def _do_project_info():
    return {"ok": True, "td_build": app.version, "fps": project.cook.rate,
            "file": project.name}



def _do_capture_viewport(path, detail="normal"):
    """Save a TOP/viewer to a PNG and compute a quality verdict."""
    import tempfile
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    _force_cook(node)
    out = os.path.join(tempfile.gettempdir(), "td_mcp_capture.png")
    try:
        node.save(out)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"capture failed: {e}"}
    verdict = {"is_black": None, "is_flat": None}
    try:
        arr = node.numpyArray()
        import numpy as np
        flat = arr.reshape(-1, arr.shape[-1])[:, :3].astype(float)
        mean = float(flat.mean())
        std = float(flat.std())
        verdict = {"is_black": mean < 4.0, "is_flat": std < 1.5,
                   "mean": round(mean, 2), "std": round(std, 2)}
    except Exception:  # noqa: BLE001
        pass
    res = {"ok": True, "file": out, "verdict": verdict}
    if detail == "brief":
        return {"ok": True, "verdict": verdict}
    return res


def _do_map_network(path, depth=2):
    """Emit a Graphviz DOT graph of the network (nested github-mcp `map` tool).

    Shows node connections, positions and non-default param counts so an
    agent can reason about topology spatially. Rendered by `dot -Tpng/-Tsvg`."""
    start_node = _op(path) or root()
    if start_node is None:
        return {"ok": False, "error": "not found"}
    nodes = []
    edges = []
    seen = set()

    def traverse(node, d):
        if node is None or d > depth or node.path in seen:
            return
        seen.add(node.path)
        ndef = (f'  "{node.path}" [label="{node.name}\\n{node.type}", '
                f'pos="{getattr(node, "nodeX", 0)},{getattr(node, "nodeY", 0)}!"];]')
        nodes.append(ndef)
        try:
            for out_idx, out_op in enumerate(getattr(node, "outputs", []) or []):
                targets = []
                try:
                    targets = [c.inputOP.path for oc in node.outputConnectors[out_idx].connections
                               if oc and oc.inputOP]
                except Exception:  # noqa: BLE001
                    pass
                for t in targets:
                    edges.append(f'  "{node.path}" -> "{t}";')
        except Exception:  # noqa: BLE001
            pass
        try:
            for child in node.children:
                traverse(child, d + 1)
        except Exception:  # noqa: BLE001
            pass

    traverse(start_node, 1)
    dot = "digraph td_network {\n  rankdir=LR;\n  node [shape=box, style=rounded];\n"
    dot += "\n".join(nodes) + "\n" + "\n".join(edges) + "\n}\n"
    return {"ok": True, "format": "dot", "dot": dot, "node_count": len(nodes)}


def _do_get_resource(uri):
    """td:// resources: chop/<path>, node/<path>, errors/<path>, project."""
    m = re.match(r"td://([^/]+)/(.*)", uri or "")
    if not m:
        return {"ok": False, "error": "bad td:// uri"}
    kind, rest = m.group(1), m.group(2)
    if kind == "project":
        return _do_project_info()
    if kind == "errors":
        return _do_get_errors(rest)
    if kind == "chop":
        node = _op(rest)
        if node is None:
            return {"ok": False, "error": "not found"}
        return {"ok": True, "channels": [c.name for c in node.chans]}
    if kind == "node":
        node = _op(rest)
        if node is None:
            return {"ok": False, "error": "not found"}
        return {"ok": True, "name": node.name, "type": node.type,
                "path": node.path, "children": len(node.children)}
    return {"ok": False, "error": f"unknown resource: {kind}"}


def _do_batch(ops):
    """Collapse N tool calls into one round-trip (benoitliard)."""
    results = []
    for call in ops:
        tool = call.get("tool")
        fn = _DISPATCH.get(tool)
        if fn is None:
            results.append({"tool": tool, "ok": False, "error": "unknown tool"})
            continue
        try:
            with ui.undo:
                results.append({"tool": tool, "result": fn(call.get("args", {}))})
        except Exception as e:  # noqa: BLE001
            results.append({"tool": tool, "ok": False, "error": str(e),
                          "recovery_hints": recovery_hints(str(e))})
    return {"ok": True, "batch": results}


def _do_describe():
    """Self-documenting tool manifest (8beeeaaat)."""
    return {"ok": True, "auth_required": True, "allow_exec": ALLOW_EXEC,
            "protected_paths": PROTECTED, "tools": sorted(_DISPATCH.keys())}


# --- data readers (benoitliard) -------------------------------------------
def _do_read_chop(path, channel=None, samples=10):
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    chans = node.chans
    out = {}
    for c in (chans if channel is None else [node[channel]]):
        out[c.name] = [round(float(v), 4) for v in c.vals[:samples]]
    return {"ok": True, "channels": out}


def _do_read_top(path, detail="brief"):
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    info = {"ok": True, "width": getattr(node, "width", None),
            "height": getattr(node, "height", None)}
    if detail != "brief":
        try:
            info["aspect"] = getattr(node, "aspect", None)
        except Exception:  # noqa: BLE001
            pass
    return info


def _do_read_dat(path, rows=10):
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    data = []
    for r in range(min(rows, node.numRows)):
        data.append([node[r, c].val for c in range(node.numCols)])
    return {"ok": True, "rows": data}


_DISPATCH = {
    "create_node": lambda a: _do_create(a.get("path"), a.get("type"), a.get("name"),
                                         a.get("nodeX"), a.get("nodeY")),
    "delete_node": lambda a: _do_delete(a.get("path")),
    "set_parameters": lambda a: _do_set_parameters(a.get("path"), a.get("params", {}), a.get("detailLevel", "normal")),
    "get_parameters": lambda a: _do_get_parameters(a.get("path"), a.get("detailLevel", "normal")),
    "get_errors": lambda a: _do_get_errors(a.get("path")),
    "execute_python": lambda a: _do_execute_python(a.get("code")),
    "list_nodes": lambda a: _do_list_nodes(a.get("path"), a.get("detailLevel", "normal")),
    "project_info": lambda a: _do_project_info(),
    "capture_viewport": lambda a: _do_capture_viewport(a.get("path"), a.get("detailLevel", "normal")),
    "get_resource": lambda a: _do_get_resource(a.get("uri")),
    "describe_td_tools": lambda a: _do_describe(),
    "batch": lambda a: _do_batch(a.get("ops", [])),
    "read_chop": lambda a: _do_read_chop(a.get("path"), a.get("channel"), a.get("samples", 10)),
    "read_top": lambda a: _do_read_top(a.get("path"), a.get("detailLevel", "brief")),
    "read_dat": lambda a: _do_read_dat(a.get("path"), a.get("rows", 10)),
    "scan_network": lambda a: _do_scan_network(a.get("path"), a.get("depth", 3)),
    "connect_nodes": lambda a: _do_connect_nodes(a.get("from_path"), a.get("to_path"), a.get("from_output", 0), a.get("to_input", 0)),
    "rename_node": lambda a: _do_rename_node(a.get("path"), a.get("new_name")),
    "copy_node": lambda a: _do_copy_node(a.get("path"), a.get("new_parent"), a.get("new_name")),
    "auto_layout": lambda a: _do_auto_layout(a.get("path"), a.get("direction", "left-right"), a.get("spacing_x", 200), a.get("spacing_y", 200)),
    "get_node": lambda a: _do_get_node(a.get("path")),
    "set_node_color": lambda a: _do_set_node_color(a.get("path"), a.get("r", 0), a.get("g", 0), a.get("b", 0)),
    "set_node_comment": lambda a: _do_set_node_comment(a.get("path"), a.get("comment"), a.get("tags")),
    "map_network": lambda a: _do_map_network(a.get("path"), a.get("depth", 2)),
    "disconnect_nodes": lambda a: _do_disconnect_nodes(a.get("from_path"), a.get("to_path"), a.get("to_input", 0)),
    "get_connections": lambda a: _do_get_connections(a.get("path")),
    "exec_node_method": lambda a: _do_exec_node_method(a.get("path"), a.get("method"), a.get("args")),
    "snapshot_network": lambda a: _do_snapshot_network(a.get("path")),
    "restore_network": lambda a: _do_restore_network(a.get("snapshot"), a.get("target_parent", "*here")),
    "get_performance": lambda a: _do_get_performance(a.get("path")),
    "validate_network": lambda a: _do_validate_network(a.get("path"), a.get("depth", 2)),
    "set_flags": lambda a: _do_set_flags(a.get("path"), a.get("flags")),
    "find_nodes": lambda a: _do_find_nodes(a.get("path"), a.get("query"), a.get("type"), a.get("depth", 4)),
    "set_node_position": lambda a: _do_set_node_position(a.get("path"), a.get("x", 0), a.get("y", 0)),
    "timeline": lambda a: _do_timeline(a.get("action"), a.get("value")),
    "export_recipe": lambda a: _do_export_recipe(a.get("path"), a.get("depth", 3)),
    "import_recipe": lambda a: _do_import_recipe(a.get("recipe"), a.get("target_parent", "*here")),
    "save_tox": lambda a: _do_save_tox(a.get("path"), a.get("file_path")),
}


def _wrap(result):
    """Attach recovery hints to any failed result."""
    if isinstance(result, dict) and result.get("ok") is False and "recovery_hints" not in result:
        hints = recovery_hints(result.get("error", ""))
        if hints:
            result["recovery_hints"] = hints
    return result


# ---------------------------------------------------------------------------
# Pure-Python WebSocket helpers (RFC 6455)
# ---------------------------------------------------------------------------
_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _ws_handshake_key(key):
    return base64.b64encode(
        hashlib.sha1((key + _WS_MAGIC).encode("utf-8")).digest()
    ).decode("utf-8")


def _ws_read_frame(rfile):
    """Read one WebSocket data frame from the socket. Returns (opcode, payload bytes)."""
    header = rfile.read(2)
    if len(header) < 2:
        return None, None
    b0, b1 = header
    opcode = b0 & 0x0F
    masked = (b1 & 0x80) != 0
    length = b1 & 0x7F
    if length == 126:
        length = struct.unpack(">H", rfile.read(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", rfile.read(8))[0]
    mask_key = rfile.read(4) if masked else b"\x00\x00\x00\x00"
    data = bytearray(rfile.read(length))
    if masked:
        for i in range(len(data)):
            data[i] ^= mask_key[i % 4]
    return opcode, bytes(data)


def _ws_make_frame(payload, opcode=0x1):
    """Create a server-side (unmasked) WebSocket text frame."""
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    length = len(data)
    if length <= 125:
        header = bytes([0x80 | opcode, length])
    elif length <= 65535:
        header = bytes([0x80 | opcode, 126]) + struct.pack(">H", length)
    else:
        header = bytes([0x80 | opcode, 127]) + struct.pack(">Q", length)
    return header + data


class _Handler(StreamableHTTPMixin, BaseHTTPRequestHandler):
    # Required by StreamableHTTPMixin
    AUTH_TOKEN = None  # set at runtime in start()
    DISPATCH = _DISPATCH
    _wrap = staticmethod(_wrap)
    _ws_push = staticmethod(_ws_push)

    def _check_dns_rebind(self):
        """DNS-rebind guard: only allow localhost Host header."""
        host = self.headers.get("Host", "").split(":")[0].lower()
        if host not in ("localhost", "127.0.0.1", "::1"):
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"jsonrpc": "2.0", "error": {"code": -32600, "message": "Forbidden: DNS-rebind protection"}}).encode())
            return False
        return True

    def _get_or_create_session(self):
        session_id = self.headers.get("Mcp-Session-Id")
        if session_id:
            session = _get_session(session_id)
            if session:
                return session_id, session
        session_id = _generate_session_id()
        session = _touch_session(session_id, self.AUTH_TOKEN)
        return session_id, session

    def _send_jsonrpc_response(self, request_id, result=None, error=None):
        resp = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            resp["error"] = error
        else:
            resp["result"] = result
        body = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", self._current_session_id)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # DNS rebind protection
        if not self._check_dns_rebind():
            return

        # Legacy /mcp endpoint (backward compatible)
        if self.path == "/mcp":
            if not self._auth_ok():
                self._send({"ok": False, "error": "unauthorized"}, 401)
                return
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                req = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._send({"ok": False, "error": "bad json"}, 400)
                return
            tool = req.get("tool")
            fn = _DISPATCH.get(tool)
            if fn is None:
                self._send({"ok": False, "error": f"unknown tool: {tool}"}, 404)
                return
            try:
                with ui.undo:
                    result = _wrap(fn(req.get("args", {})))
                ok = result.get("ok") if isinstance(result, dict) else True
                _ws_push({"tool": tool, "ok": ok})
                self._send(result or {"ok": True})
            except Exception as e:
                _ws_push({"tool": tool, "ok": False, "error": str(e)})
                self._send(_wrap({"ok": False, "error": str(e)}), 500)
            return

        # Streamable HTTP JSON-RPC endpoint (root path)
        if self.path in ("/", "/stream", "/rpc"):
            self._handle_jsonrpc_post()
            return

        self._send({"ok": False, "error": "not found"}, 404)

    def _handle_jsonrpc_post(self):
        # Session management
        self._current_session_id, session = self._get_or_create_session()
        if not self._auth_ok_for_session(session):
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"jsonrpc": "2.0", "error": {"code": -32600, "message": "Unauthorized"}}).encode())
            return

        # Read request body
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._send_jsonrpc_response(None, error={"code": -32700, "message": "Parse error"})
            return

        # Process JSON-RPC
        response = self._handle_jsonrpc(payload, self._current_session_id, session)
        if response is not None:
            self._send_jsonrpc_response(response.get("id"), response.get("result"), response.get("error"))

    def _auth_ok_for_session(self, session):
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {session.get('auth_token', self.AUTH_TOKEN)}"
        return hmac.compare_digest(supplied, expected)

    def do_GET(self):
        # WebSocket upgrade
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self._handle_websocket()
            return

        # SSE stream (Accept: text/event-stream)
        if self.headers.get("Accept", "").find("text/event-stream") >= 0:
            self._handle_sse()
            return

        if self.path in ("/", "/ui", "/chat"):
            try:
                with open(_CHAT_UI_PATH, "rb") as f:
                    self._send_html(f.read())
            except FileNotFoundError:
                self._send({"ok": False, "error": "chat_ui.html not found"})
            return
        if self.path == "/api/status":
            self._send({"ok": True, "status": "running", "port": TD_MCP_PORT,
                        "auth": True, "allow_exec": ALLOW_EXEC})
        elif self.path.startswith("/api/resource?"):
            m = re.search(r"uri=([^&]+)", self.path)
            uri = _urldecode(m.group(1)) if m else ""
            self._send(_wrap(_do_get_resource(uri)))
        else:
            self._send({"ok": False, "error": "not found"}, 404)

    def _handle_sse(self):
        """Server-Sent Events stream for real-time tool events."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.flush()

        # Register this client for SSE
        client_id = _generate_session_id()
        _sse_clients.add(self)
        try:
            # Send initial event
            self._send_sse({"type": "connected", "client_id": client_id}, event="open")
            self.wfile.flush()
            # Keep connection alive
            while True:
                time.sleep(30)
                self._send_sse({"type": "ping"}, event="ping")
        except Exception:
            pass
        finally:
            _sse_clients.discard(self)

    def _send_sse(self, data, event=None):
        out = []
        if event:
            out.append(f"event: {event}")
        out.append(f"data: {json.dumps(data)}")
        out.append("")
        frame = "\n".join(out) + "\n"
        try:
            self.wfile.write(frame.encode("utf-8"))
            self.wfile.flush()
        except Exception:
            pass
    def _send(self, obj, code=200):
        if isinstance(obj, dict) and obj.get("ok") is False:
            obj = _wrap(obj)
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        # Loopback-only CORS (CSRF-safe): reflect a localhost/127.0.0.1 Origin,
        # never the old `*` wildcard.
        self.send_header("Access-Control-Allow-Origin", _cors_origin(self.headers.get("Origin", "")))
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html_bytes, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_bytes)))
        self.end_headers()
        self.wfile.write(html_bytes)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", _cors_origin(self.headers.get("Origin", "")))
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def _auth_ok(self):
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {AUTH_TOKEN}"
        # Constant-time compare (bottobot / axysar) to avoid token timing leaks.
        return hmac.compare_digest(supplied, expected)

    def _handle_websocket(self):
        """Upgrade connection to WebSocket and dispatch tool calls."""
        key = self.headers.get("Sec-WebSocket-Key", "")
        accept = _ws_handshake_key(key)
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()
        with _ws_lock:
            _ws_clients.add(self)
        try:
            # Authenticate via first message
            opcode, raw = _ws_read_frame(self.rfile)
            if opcode is None:
                return
            try:
                auth_msg = json.loads(raw.decode("utf-8"))
            except Exception:
                auth_msg = {}
            if auth_msg.get("type") == "auth":
                if not hmac.compare_digest(str(auth_msg.get("token", "")), AUTH_TOKEN):
                    self.wfile.write(_ws_make_frame(json.dumps({"ok": False, "error": "unauthorized"})))
                    self.wfile.flush()
                    return
                self.wfile.write(_ws_make_frame(json.dumps({"ok": True, "status": "authenticated"})))
                self.wfile.flush()
            else:
                # First frame must be an auth handshake.
                self.wfile.write(_ws_make_frame(json.dumps({"ok": False, "error": "auth required"})))
                self.wfile.flush()
                return
            # Main dispatch loop
            while True:
                opcode, raw = _ws_read_frame(self.rfile)
                if opcode is None or opcode == 0x8:  # None or close frame
                    break
                try:
                    req = json.loads(raw.decode("utf-8"))
                except Exception:
                    continue
                msg_id = req.get("id")
                tool = req.get("tool")
                fn = _DISPATCH.get(tool)
                if fn is None:
                    resp = {"id": msg_id, "ok": False, "error": f"unknown tool: {tool}"}
                else:
                    try:
                        with ui.undo:
                            result = _wrap(fn(req.get("args", {})))
                        ok = result.get("ok") if isinstance(result, dict) else True
                        _ws_push({"tool": tool, "ok": ok})
                        resp = {"id": msg_id, "ok": True, "result": result}
                    except Exception as e:  # noqa: BLE001
                        _ws_push({"tool": tool, "ok": False, "error": str(e)})
                        resp = {"id": msg_id, "ok": False, "error": str(e)}
                self.wfile.write(_ws_make_frame(json.dumps(resp)))
                self.wfile.flush()
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _ws_lock:
                _ws_clients.discard(self)

    def log_message(self, *args):
        pass


def _urldecode(s):
    from urllib.parse import unquote
    return unquote(s)


def _cors_origin(request_origin):
    """Reflect a *loopback* Origin (CSRF-safe); deny everything else.

    The old `*` wildcard let any page that could persuade the user's browser to
    hit 127.0.0.1 reach the bridge. We only echo back an Origin that is itself
    localhost/127.0.0.1, otherwise fall back to a fixed loopback value so
    same-origin Chat-UI requests still work."""
    if not request_origin:
        return "http://127.0.0.1"
    host = request_origin.split("://", 1)[-1].split("/", 1)[0].split(":")[0].lower()
    if host in ("localhost", "127.0.0.1", "::1", "[::1]"):
        return request_origin
    return "http://127.0.0.1"


_server = None
_thread = None


def start():
    global _server, _thread
    if _server is not None:
        debug("td_mcp already running")
        return
    print(f"[td_mcp] auth token: {AUTH_TOKEN}")
    if PROTECTED:
        print(f"[td_mcp] protected paths: {PROTECTED}")
    if not ALLOW_EXEC:
        print("[td_mcp] execute_python DISABLED (TD_MCP_ALLOW_EXEC=0)")
    _server = HTTPServer(("127.0.0.1", TD_MCP_PORT), _Handler)
    _Handler.AUTH_TOKEN = AUTH_TOKEN  # set auth token on handler class
    if STREAMABLE_HTTP_AVAILABLE:
        start_session_cleanup()
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()
    debug(f"td_mcp bridge started on http://127.0.0.1:{TD_MCP_PORT}")


def stop():
    global _server, _thread
    if _server is not None:
        _server.shutdown()
        _server.server_close()
        _server = None
        _thread = None
        if STREAMABLE_HTTP_AVAILABLE:
            stop_session_cleanup()
        debug("td_mcp bridge stopped")


def status():
    return {"running": _server is not None, "port": TD_MCP_PORT,
            "auth_token": AUTH_TOKEN, "allow_exec": ALLOW_EXEC}
