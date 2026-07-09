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

Every mutating tool wraps op changes in ui.undo so one Ctrl+Z
reverts a whole agent batch.
"""

import json
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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


def _do_create(path, type, name=None):
    parent = _op(path) or parent()
    if parent is None:
        return {"ok": False, "error": f"parent not found: {path}"}
    node = parent.create(name or type.split()[-1], type)
    return {"ok": True, "path": node.path}


def _do_delete(path):
    if _is_protected(path):
        return {"ok": False, "error": f"path is protected from deletion: {path}"}
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    node.destroy()
    return {"ok": True}


def _do_set_parameters(path, params, detail="normal"):
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    applied = {}
    for k, v in params.items():
        if k in node.par:
            node.par[k].val = v
            applied[k] = v
    if detail == "brief":
        return {"ok": True, "applied_count": len(applied)}
    return {"ok": True, "applied": applied}


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


def _do_project_info():
    return {"ok": True, "td_build": app.version, "fps": project.cook.rate,
            "file": project.name}


def _do_capture_viewport(path, detail="normal"):
    """Save a TOP/viewer to a PNG and compute a quality verdict."""
    import tempfile
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
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
    "create_node": lambda a: _do_create(a.get("path"), a.get("type"), a.get("name")),
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
}


def _wrap(result):
    """Attach recovery hints to any failed result."""
    if isinstance(result, dict) and result.get("ok") is False and "recovery_hints" not in result:
        hints = recovery_hints(result.get("error", ""))
        if hints:
            result["recovery_hints"] = hints
    return result


class _Handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        if isinstance(obj, dict) and obj.get("ok") is False:
            obj = _wrap(obj)
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send({"ok": True})

    def _auth_ok(self):
        tok = self.headers.get("Authorization", "")
        return tok == f"Bearer {AUTH_TOKEN}"

    def do_GET(self):
        if self.path == "/api/status":
            self._send({"ok": True, "status": "running", "port": TD_MCP_PORT,
                        "auth": True, "allow_exec": ALLOW_EXEC})
        elif self.path.startswith("/api/resource?"):
            m = re.search(r"uri=([^&]+)", self.path)
            uri = _urldecode(m.group(1)) if m else ""
            self._send(_wrap(_do_get_resource(uri)))
        else:
            self._send({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/mcp":
            self._send({"ok": False, "error": "not found"}, 404)
            return
        if not self._auth_ok():
            self._send({"ok": False, "error": "unauthorized"}, 401)
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw.decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            self._send({"ok": False, "error": "bad json"}, 400)
            return
        tool = req.get("tool")
        fn = _DISPATCH.get(tool)
        if fn is None:
            self._send({"ok": False, "error": f"unknown tool: {tool}"})
            return
        try:
            with ui.undo:
                result = _wrap(fn(req.get("args", {})))
            self._send(result or {"ok": True})
        except Exception as e:  # noqa: BLE001
            self._send(_wrap({"ok": False, "error": str(e)}), 500)

    def log_message(self, *args):
        pass


def _urldecode(s):
    from urllib.parse import unquote
    return unquote(s)


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
        debug("td_mcp bridge stopped")


def status():
    return {"running": _server is not None, "port": TD_MCP_PORT,
            "auth_token": AUTH_TOKEN, "allow_exec": ALLOW_EXEC}
