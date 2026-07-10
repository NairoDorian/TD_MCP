"""td-mcp live server: Streamable HTTP MCP server for TouchDesigner.

Implements the MCP Streamable HTTP spec (8beeeaaat / TD_Builder_alpha style):
- JSON-RPC over POST (initialize, tools/list, tools/call, prompts/list, resources/list, etc.)
- Server-Sent Events (SSE) for server→client streaming
- Multi-session via Mcp-Session-Id header
- DNS-rebind protection (Host must be localhost)
- Backward-compatible stdio mode for Claude Desktop / Cursor
- Talks to TD bridge at http://127.0.0.1:9980 (or TD_MCP_HOST:TD_MCP_PORT)

CLI:
  python -m td_mcp.server_live                 # stdio MCP (Claude Desktop)
  python -m td_mcp.server_live --http          # Streamable HTTP on 127.0.0.1:8765
  python -m td_mcp.server_live create ...      # legacy CLI against bridge
"""

import argparse
import hmac
import json
import os
import sys
import threading
import time
import uuid
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

try:
    import websocket
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False

try:
    from mcp.server import Server
    import mcp.types as types
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9980
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8765

# Session management
_sessions = {}  # session_id -> {"created": float, "last_seen": float, "auth_token": str}
_sessions_lock = threading.Lock()
SESSION_TTL = 3600
SESSION_CLEANUP_INTERVAL = 300

# SSE clients
_sse_clients = set()
_sse_lock = threading.Lock()

ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Host/Origin pinning (Embody-style DNS-rebind / CSRF defense). When set, the
# Origin header must match one of these exact values; an empty set disables the
# check (loopback-only Host check still applies). Configure via TD_MCP_ALLOWED_ORIGINS.
ALLOWED_ORIGINS = set(
    o for o in (os.environ.get("TD_MCP_ALLOWED_ORIGINS") or "").split(",") if o
)


def _validate_origin_header(origin_header):
    if not ALLOWED_ORIGINS:
        return True
    return (origin_header or "") in ALLOWED_ORIGINS


def _generate_session_id():
    return str(uuid.uuid4())


def _cleanup_sessions():
    now = time.time()
    with _sessions_lock:
        dead = [sid for sid, s in _sessions.items() if now - s["last_seen"] > SESSION_TTL]
        for sid in dead:
            del _sessions[sid]


def _touch_session(session_id, auth_token=None):
    with _sessions_lock:
        if session_id not in _sessions:
            _sessions[session_id] = {"created": time.time(), "last_seen": time.time(), "auth_token": auth_token}
        else:
            _sessions[session_id]["last_seen"] = time.time()
            if auth_token:
                _sessions[session_id]["auth_token"] = auth_token
        return _sessions[session_id]


def _get_session(session_id):
    with _sessions_lock:
        s = _sessions.get(session_id)
        if s and time.time() - s["last_seen"] <= SESSION_TTL:
            s["last_seen"] = time.time()
            return s
        elif s:
            del _sessions[session_id]
    return None


def _validate_host_header(host_header):
    if not host_header:
        return False
    host = host_header.split(":")[0].lower()
    return host in ALLOWED_HOSTS


def _sse_push(event_type, data):
    frame = []
    if event_type:
        frame.append(f"event: {event_type}")
    frame.append(f"data: {json.dumps(data)}")
    frame.append("")
    payload = "\n".join(frame) + "\n"
    with _sse_lock:
        drop = []
        for client in _sse_clients:
            try:
                client.write(payload.encode("utf-8"))
                client.flush()
            except Exception:
                drop.append(client)
        for client in drop:
            _sse_clients.discard(client)


# ---------------------------------------------------------------------------
# Bridge HTTP client (unchanged API, just used by tools)
# ---------------------------------------------------------------------------
class TDClient:
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=30,
                 anchor=False, auth_token=None):
        self.base = f"http://{host}:{port}"
        self.timeout = timeout
        self.anchor = anchor
        self.auth_token = auth_token or os.environ.get("TD_MCP_AUTH_TOKEN")

    def _anchor(self, tool, args):
        try:
            from td_mcp.rag.retriever import build_retriever
            from td_mcp.rag.strategies import ParallelRetriever
        except Exception:  # noqa: BLE001
            return []
        q = ""
        if tool == "create_node":
            q = f"{args.get('type', '')} operator parameters"
        elif tool == "set_parameters":
            q = f"{args.get('path', '')} parameter names"
        elif tool == "execute_python":
            q = args.get("code", "")
        if not q.strip():
            return []
        pr = ParallelRetriever(build_retriever())
        return [{"title": c.get("title"), "source": c.get("source"),
                  "text": c.get("text", "")[:280]}
                 for c, _ in pr.search(q, k=2)]

    def _call(self, tool, args=None):
        payload = json.dumps({"tool": tool, "args": args or {}}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        req = urllib.request.Request(f"{self.base}/mcp", data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                result = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')}"
            return self._fail(tool, err)
        except Exception as e:  # noqa: BLE001
            return self._fail(tool, str(e))
        if not isinstance(result, dict):
            return result
        # Attach Embody-style recovery hints to any failed bridge response so
        # an agent can self-correct instead of retrying blindly.
        if result.get("ok") is False and isinstance(result.get("error"), str):
            try:
                from td_mcp.tools.recovery import attach_recovery
                result = attach_recovery(result, tool=tool)
            except Exception:  # noqa: BLE001
                pass
        if self.anchor and result.get("ok"):
            result["shots"] = self._anchor(tool, args or {})
        return result

    @staticmethod
    def _fail(tool, err):
        try:
            from td_mcp.tools.recovery import attach_to_error
            return attach_to_error(err, tool=tool)
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": err}

    # All bridge tools as methods
    def create_node(self, path, op_type, name=None):
        return self._call("create_node", {"path": path, "type": op_type, "name": name})

    def delete_node(self, path):
        return self._call("delete_node", {"path": path})

    def set_parameters(self, path, params):
        return self._call("set_parameters", {"path": path, "params": params})

    def get_parameters(self, path):
        return self._call("get_parameters", {"path": path})

    def get_errors(self, path):
        return self._call("get_errors", {"path": path})

    def execute_python(self, code):
        return self._call("execute_python", {"code": code})

    def list_nodes(self, path):
        return self._call("list_nodes", {"path": path})

    def project_info(self):
        return self._call("project_info", {})

    def capture_viewport(self, path, detail="normal"):
        return self._call("capture_viewport", {"path": path, "detailLevel": detail})

    def get_resource(self, uri):
        return self._call("get_resource", {"uri": uri})

    def describe_td_tools(self):
        return self._call("describe_td_tools", {})

    def batch(self, ops):
        return self._call("batch", {"ops": ops})

    def read_chop(self, path, channel=None, samples=10):
        return self._call("read_chop", {"path": path, "channel": channel, "samples": samples})

    def read_top(self, path, detail="brief"):
        return self._call("read_top", {"path": path, "detailLevel": detail})

    def read_dat(self, path, rows=10):
        return self._call("read_dat", {"path": path, "rows": rows})

    def scan_network(self, path, depth=3):
        return self._call("scan_network", {"path": path, "depth": depth})

    def build_and_verify(self, path, op_type, params=None):
        created = self.create_node(path, op_type)
        if not created.get("ok"):
            return created
        node_path = created.get("path")
        if params:
            self.set_parameters(node_path, params)
        errs = self.get_errors(node_path)
        verdict = self.capture_viewport(node_path)
        return {"created": created, "errors": errs, "preview": verdict}

    def connect_nodes(self, from_path, to_path, from_output=0, to_input=0):
        return self._call("connect_nodes", {"from_path": from_path, "to_path": to_path,
                                            "from_output": from_output, "to_input": to_input})

    def rename_node(self, path, new_name):
        return self._call("rename_node", {"path": path, "new_name": new_name})

    def copy_node(self, path, new_parent, new_name=None):
        return self._call("copy_node", {"path": path, "new_parent": new_parent, "new_name": new_name})

    def auto_layout(self, path, direction="left-right", spacing_x=200, spacing_y=200):
        return self._call("auto_layout", {"path": path, "direction": direction,
                                           "spacing_x": spacing_x, "spacing_y": spacing_y})

    def get_node(self, path):
        return self._call("get_node", {"path": path})

    def set_node_color(self, path, r, g, b):
        return self._call("set_node_color", {"path": path, "r": r, "g": g, "b": b})

    def set_node_comment(self, path, comment=None, tags=None):
        return self._call("set_node_comment", {"path": path, "comment": comment, "tags": tags})

    def map_network(self, path, depth=2):
        return self._call("map_network", {"path": path, "depth": depth})

    def disconnect_nodes(self, from_path, to_path, to_input=0):
        return self._call("disconnect_nodes", {"from_path": from_path, "to_path": to_path, "to_input": to_input})

    def get_connections(self, path):
        return self._call("get_connections", {"path": path})

    def exec_node_method(self, path, method, args=None):
        return self._call("exec_node_method", {"path": path, "method": method, "args": args or []})

    def snapshot_network(self, path):
        return self._call("snapshot_network", {"path": path})

    def restore_network(self, snapshot, target_parent="*here"):
        return self._call("restore_network", {"snapshot": snapshot, "target_parent": target_parent})

    def get_performance(self, path):
        return self._call("get_performance", {"path": path})

    def validate_network(self, path, depth=2):
        return self._call("validate_network", {"path": path, "depth": depth})

    def set_flags(self, path, flags):
        return self._call("set_flags", {"path": path, "flags": flags})

    def find_nodes(self, path, query=None, type=None, depth=4):
        return self._call("find_nodes", {"path": path, "query": query, "type": type, "depth": depth})

    def set_node_position(self, path, x, y):
        return self._call("set_node_position", {"path": path, "x": x, "y": y})

    def timeline(self, action, value=None):
        return self._call("timeline", {"action": action, "value": value})

    def export_recipe(self, path, depth=3):
        return self._call("export_recipe", {"path": path, "depth": depth})

    def import_recipe(self, recipe, target_parent="*here"):
        return self._call("import_recipe", {"recipe": recipe, "target_parent": target_parent})

    def save_tox(self, path, file_path=None):
        return self._call("save_tox", {"path": path, "file_path": file_path})


# ---------------------------------------------------------------------------
# Streamable HTTP Request Handler (MCP over HTTP + SSE)
# ---------------------------------------------------------------------------
class MCPStreamableHandler(BaseHTTPRequestHandler):
    """Handles MCP Streamable HTTP (POST /) and SSE (GET /)."""

    def _check_dns_rebind(self):
        host = self.headers.get("Host", "")
        if not _validate_host_header(host):
            self._send_error(403, -32600, "Forbidden: DNS-rebind protection")
            return False
        return True

    def _get_or_create_session(self):
        session_id = self.headers.get("Mcp-Session-Id")
        if session_id:
            session = _get_session(session_id)
            if session:
                return session_id, session
        session_id = _generate_session_id()
        session = _touch_session(session_id)
        return session_id, session

    def _auth_ok(self, session):
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {session.get('auth_token', '')}"
        return hmac.compare_digest(supplied, expected)

    def _send_jsonrpc(self, request_id, result=None, error=None):
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

    def _send_sse(self, event_type, data):
        out = []
        if event_type:
            out.append(f"event: {event_type}")
        out.append(f"data: {json.dumps(data)}")
        out.append("")
        frame = "\n".join(out) + "\n"
        self.wfile.write(frame.encode("utf-8"))
        self.wfile.flush()

    def do_POST(self):
        if not self._check_dns_rebind():
            return
        if not _validate_origin_header(self.headers.get("Origin")):
            self._send_error(403, -32600, "Forbidden: Origin not pinned")
            return

        session_id, session = self._get_or_create_session()
        self._current_session_id = session_id

        if not self._auth_ok(session):
            self._send_jsonrpc(None, error={"code": -32600, "message": "Unauthorized"})
            return

        if "application/json" not in self.headers.get("Content-Type", ""):
            self._send_error(415, -32600, "Content-Type must be application/json")
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._send_jsonrpc(None, error={"code": -32700, "message": "Parse error"})
            return

        # Handle batch (array) or single request
        requests = payload if isinstance(payload, list) else [payload]
        responses = []

        for req in requests:
            req_id = req.get("id")
            method = req.get("method")
            params = req.get("params", {})

            if method == "initialize":
                responses.append({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}, "prompts": {}, "resources": {}, "logging": {}},
                        "serverInfo": {"name": "td-mcp-live", "version": "1.1.0"}
                    }
                })
            elif method == "notifications/initialized":
                # Client ack - no response
                pass
            elif method == "tools/list":
                tool_list = _http_tool_meta()
                responses.append({"jsonrpc": "2.0", "id": req_id, "result": {"tools": tool_list}})
            elif method == "tools/call":
                tool_name = params.get("name")
                args = params.get("arguments", {})
                fn = TOOL_REGISTRY.get(tool_name)
                if fn is None:
                    responses.append({"jsonrpc": "2.0", "id": req_id,
                                     "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}})
                else:
                    try:
                        result = fn(args)
                        responses.append({
                            "jsonrpc": "2.0", "id": req_id,
                            "result": {"content": [{"type": "text", "text": json.dumps(result)}],
                                      "isError": result.get("ok") is False}
                        })
                        ok = result.get("ok") if isinstance(result, dict) else True
                        _sse_push("tool_result", {"tool": tool_name, "ok": ok})
                    except Exception as e:
                        responses.append({"jsonrpc": "2.0", "id": req_id,
                                         "error": {"code": -32603, "message": str(e)}})
            elif method == "prompts/list":
                responses.append({"jsonrpc": "2.0", "id": req_id, "result": {"prompts": []}})
            elif method == "resources/list":
                responses.append({"jsonrpc": "2.0", "id": req_id, "result": {"resources": []}})
            elif method == "resources/templates/list":
                responses.append({"jsonrpc": "2.0", "id": req_id, "result": {"resourceTemplates": []}})
            else:
                responses.append({"jsonrpc": "2.0", "id": req_id,
                                 "error": {"code": -32601, "message": f"Method not found: {method}"}})

        # Batch returns array, single returns single object
        if isinstance(payload, list):
            self._send_jsonrpc(None, result=responses)
        else:
            first = responses[0] if responses else None
            self._send_jsonrpc(first.get("id") if first else None,
                              result=first.get("result") if first else None)

    def do_GET(self):
        if not self._check_dns_rebind():
            return
        if not _validate_origin_header(self.headers.get("Origin")):
            self._send_error(403, -32600, "Forbidden: Origin not pinned")
            return

        # SSE stream
        accept = self.headers.get("Accept", "")
        if "text/event-stream" in accept:
            self._handle_sse()
            return

        # Health/status
        if self.path in ("/", "/health", "/api/status"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "status": "running"}).encode())
            return

        self._send_error(404, -32601, "Not found")

    def _handle_sse(self):
        session_id = self.headers.get("Mcp-Session-Id")
        if not session_id:
            session_id, _ = _get_or_create_session()
        session = _get_session(session_id)
        if not session:
            self._send_error(404, -32600, "Session not found")
            return
        if not self._auth_ok(session):
            self._send_error(401, -32600, "Unauthorized")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Mcp-Session-Id", session_id)
        self.end_headers()
        self.wfile.flush()

        # Register SSE client
        with _sse_lock:
            _sse_clients.add(self.wfile)

        try:
            self._send_sse("open", {"type": "connected", "session": session_id})
            while True:
                time.sleep(30)
                self._send_sse("ping", {"type": "heartbeat", "ts": time.time()})
        except Exception:
            pass
        finally:
            with _sse_lock:
                _sse_clients.discard(self.wfile)

    def _send_error(self, http_code, jsonrpc_code, message):
        self.send_response(http_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"jsonrpc": "2.0", "error": {"code": jsonrpc_code, "message": message}}).encode())

    def log_message(self, *args):
        pass


# ---------------------------------------------------------------------------
# Tool Registry (maps tool name -> callable(args) -> result dict)
# ---------------------------------------------------------------------------
_client = TDClient()  # instantiated lazily in start()


def _get_client():
    global _client
    if _client is None:
        _client = TDClient()
    return _client


def _wrap_tool(fn):
    def wrapper(args):
        return fn(_get_client(), args)
    return wrapper


# Register all bridge tools
TOOL_REGISTRY = {
    "create_node": _wrap_tool(lambda c, a: c.create_node(a.get("path"), a.get("type"), a.get("name"))),
    "delete_node": _wrap_tool(lambda c, a: c.delete_node(a.get("path"))),
    "set_parameters": _wrap_tool(lambda c, a: c.set_parameters(a.get("path"), a.get("params", {}))),
    "get_parameters": _wrap_tool(lambda c, a: c.get_parameters(a.get("path"))),
    "get_errors": _wrap_tool(lambda c, a: c.get_errors(a.get("path"))),
    "execute_python": _wrap_tool(lambda c, a: c.execute_python(a.get("code"))),
    "list_nodes": _wrap_tool(lambda c, a: c.list_nodes(a.get("path"))),
    "project_info": _wrap_tool(lambda c, a: c.project_info()),
    "capture_viewport": _wrap_tool(lambda c, a: c.capture_viewport(a.get("path"), a.get("detail", "normal"))),
    "get_resource": _wrap_tool(lambda c, a: c.get_resource(a.get("uri"))),
    "describe_td_tools": _wrap_tool(lambda c, a: c.describe_td_tools()),
    "batch": _wrap_tool(lambda c, a: c.batch(a.get("ops", []))),
    "read_chop": _wrap_tool(lambda c, a: c.read_chop(a.get("path"), a.get("channel"), a.get("samples", 10))),
    "read_top": _wrap_tool(lambda c, a: c.read_top(a.get("path"), a.get("detail", "brief"))),
    "read_dat": _wrap_tool(lambda c, a: c.read_dat(a.get("path"), a.get("rows", 10))),
    "scan_network": _wrap_tool(lambda c, a: c.scan_network(a.get("path"), a.get("depth", 3))),
    "build_and_verify": _wrap_tool(lambda c, a: c.build_and_verify(a.get("path"), a.get("op_type"), a.get("params"))),
    "connect_nodes": _wrap_tool(lambda c, a: c.connect_nodes(a.get("from_path"), a.get("to_path"),
                                                              a.get("from_output", 0), a.get("to_input", 0))),
    "rename_node": _wrap_tool(lambda c, a: c.rename_node(a.get("path"), a.get("new_name"))),
    "copy_node": _wrap_tool(lambda c, a: c.copy_node(a.get("path"), a.get("new_parent"), a.get("new_name"))),
    "auto_layout": _wrap_tool(lambda c, a: c.auto_layout(a.get("path"), a.get("direction", "left-right"),
                                                          a.get("spacing_x", 200), a.get("spacing_y", 200))),
    "get_node": _wrap_tool(lambda c, a: c.get_node(a.get("path"))),
    "set_node_color": _wrap_tool(lambda c, a: c.set_node_color(a.get("path"), a.get("r"), a.get("g"), a.get("b"))),
    "set_node_comment": _wrap_tool(lambda c, a: c.set_node_comment(a.get("path"), a.get("comment"), a.get("tags"))),
    "map_network": _wrap_tool(lambda c, a: c.map_network(a.get("path"), a.get("depth", 2))),
    "disconnect_nodes": _wrap_tool(lambda c, a: c.disconnect_nodes(a.get("from_path"), a.get("to_path"), a.get("to_input", 0))),
    "get_connections": _wrap_tool(lambda c, a: c.get_connections(a.get("path"))),
    "exec_node_method": _wrap_tool(lambda c, a: c.exec_node_method(a.get("path"), a.get("method"), a.get("args"))),
    "snapshot_network": _wrap_tool(lambda c, a: c.snapshot_network(a.get("path"))),
    "restore_network": _wrap_tool(lambda c, a: c.restore_network(a.get("snapshot"), a.get("target_parent", "*here"))),
    "get_performance": _wrap_tool(lambda c, a: c.get_performance(a.get("path"))),
    "validate_network": _wrap_tool(lambda c, a: c.validate_network(a.get("path"), a.get("depth", 2))),
    "set_flags": _wrap_tool(lambda c, a: c.set_flags(a.get("path"), a.get("flags", {}))),
    "find_nodes": _wrap_tool(lambda c, a: c.find_nodes(a.get("path"), a.get("query"), a.get("type"), a.get("depth", 4))),
    "set_node_position": _wrap_tool(lambda c, a: c.set_node_position(a.get("path"), a.get("x"), a.get("y"))),
    "timeline": _wrap_tool(lambda c, a: c.timeline(a.get("action"), a.get("value"))),
    "export_recipe": _wrap_tool(lambda c, a: c.export_recipe(a.get("path"), a.get("depth", 3))),
    "import_recipe": _wrap_tool(lambda c, a: c.import_recipe(a.get("recipe"), a.get("target_parent", "*here"))),
    "save_tox": _wrap_tool(lambda c, a: c.save_tox(a.get("path"), a.get("file_path"))),
}


# ---------------------------------------------------------------------------
# Tool metadata — single source of truth for BOTH the stdio MCP server and
# the Streamable-HTTP server (so the HTTP `tools/list` is no longer empty and
# both surfaces share identical descriptions / schemas / risk annotations).
# ---------------------------------------------------------------------------
# (name, description, properties) — annotations are derived from risk_class().
_TOOL_META = [
    ("create_node", "Create a TouchDesigner node.", {"path": {"type": "string"}, "type": {"type": "string"}, "name": {"type": "string"}}),
    ("delete_node", "Delete a node.", {"path": {"type": "string"}}),
    ("set_parameters", "Set parameters.", {"path": {"type": "string"}, "params": {"type": "object"}}),
    ("get_parameters", "Get parameters.", {"path": {"type": "string"}}),
    ("get_errors", "Get errors.", {"path": {"type": "string"}}),
    ("execute_python", "Execute Python in TD.", {"code": {"type": "string"}}),
    ("list_nodes", "List child nodes.", {"path": {"type": "string"}}),
    ("project_info", "Project info.", {}),
    ("capture_viewport", "Capture viewport + verdict.", {"path": {"type": "string"}, "detail": {"type": "string"}}),
    ("get_resource", "Read td:// resource.", {"uri": {"type": "string"}}),
    ("describe_td_tools", "List bridge capabilities.", {}),
    ("batch", "Batch multiple ops.", {"ops": {"type": "array"}}),
    ("read_chop", "Read CHOP channels.", {"path": {"type": "string"}, "channel": {"type": "string"}, "samples": {"type": "integer"}}),
    ("read_top", "Read TOP metadata.", {"path": {"type": "string"}, "detail": {"type": "string"}}),
    ("read_dat", "Read DAT rows.", {"path": {"type": "string"}, "rows": {"type": "integer"}}),
    ("scan_network", "Scan network topology.", {"path": {"type": "string"}, "depth": {"type": "integer"}}),
    ("build_and_verify", "Create->verify->preview loop.", {"path": {"type": "string"}, "op_type": {"type": "string"}, "params": {"type": "object"}}),
    ("connect_nodes", "Wire nodes.", {"from_path": {"type": "string"}, "to_path": {"type": "string"}, "from_output": {"type": "integer"}, "to_input": {"type": "integer"}}),
    ("rename_node", "Rename node.", {"path": {"type": "string"}, "new_name": {"type": "string"}}),
    ("copy_node", "Copy node.", {"path": {"type": "string"}, "new_parent": {"type": "string"}, "new_name": {"type": "string"}}),
    ("auto_layout", "Auto-arrange nodes.", {"path": {"type": "string"}, "direction": {"type": "string"}, "spacing_x": {"type": "integer"}, "spacing_y": {"type": "integer"}}),
    ("get_node", "Get detailed node info.", {"path": {"type": "string"}}),
    ("set_node_color", "Set node color (RGB 0..1).", {"path": {"type": "string"}, "r": {"type": "number"}, "g": {"type": "number"}, "b": {"type": "number"}}),
    ("set_node_comment", "Annotate node.", {"path": {"type": "string"}, "comment": {"type": "string"}, "tags": {"type": "string"}}),
    ("map_network", "Emit Graphviz DOT.", {"path": {"type": "string"}, "depth": {"type": "integer"}}),
    ("disconnect_nodes", "Break wire.", {"from_path": {"type": "string"}, "to_path": {"type": "string"}, "to_input": {"type": "integer"}}),
    ("get_connections", "Get wiring map.", {"path": {"type": "string"}}),
    ("exec_node_method", "Call node method.", {"path": {"type": "string"}, "method": {"type": "string"}, "args": {"type": "array"}}),
    ("snapshot_network", "Save .tox checkpoint.", {"path": {"type": "string"}}),
    ("restore_network", "Restore .tox checkpoint.", {"snapshot": {"type": "string"}, "target_parent": {"type": "string"}}),
    ("get_performance", "Profile cook times.", {"path": {"type": "string"}}),
    ("validate_network", "Scene contract check.", {"path": {"type": "string"}, "depth": {"type": "integer"}}),
    ("set_flags", "Toggle node flags.", {"path": {"type": "string"}, "flags": {"type": "object"}}),
    ("find_nodes", "Search by name/type.", {"path": {"type": "string"}, "query": {"type": "string"}, "type": {"type": "string"}, "depth": {"type": "integer"}}),
    ("set_node_position", "Move node to (x,y).", {"path": {"type": "string"}, "x": {"type": "integer"}, "y": {"type": "integer"}}),
    ("timeline", "Control global timeline.", {"action": {"type": "string"}, "value": {"type": "number"}}),
    ("export_recipe", "Export recipe JSON.", {"path": {"type": "string"}, "depth": {"type": "integer"}}),
    ("import_recipe", "Import recipe JSON.", {"recipe": {"type": "object"}, "target_parent": {"type": "string"}}),
    ("save_tox", "Save COMP as .tox.", {"path": {"type": "string"}, "file_path": {"type": "string"}}),
]


def _http_tool_meta():
    from td_mcp.tools.risk import tool_annotations
    out = []
    for name, desc, props in _TOOL_META:
        out.append({
            "name": name,
            "description": desc,
            "inputSchema": {"type": "object", "properties": props},
            "annotations": tool_annotations(name),
        })
    return out


# ---------------------------------------------------------------------------
# MCP stdio server (for Claude Desktop / Cursor)
# ---------------------------------------------------------------------------
def _run_stdio_server(host=DEFAULT_HOST, port=DEFAULT_PORT, auth_token=None, anchor=False):
    if not MCP_AVAILABLE:
        print("mcp package not installed. pip install mcp", file=sys.stderr)
        sys.exit(1)

    app = Server("td-mcp-live")
    client = _get_client()

    @app.list_tools()
    async def list_tools():
        from td_mcp.tools.risk import tool_annotations
        return [
            types.Tool(name, desc, {"type": "object", "properties": props},
                       annotations=tool_annotations(name))
            for name, desc, props in _TOOL_META
        ]

    @app.call_tool()
    async def call_tool(name, arguments):
        a = arguments or {}
        try:
            fn = TOOL_REGISTRY.get(name)
            if fn is None:
                raise ValueError(f"unknown tool: {name}")
            result = fn(a)
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"error: {e}")]

    # Prompts (empty but registered for compat)
    @app.list_prompts()
    async def list_prompts():
        return []

    @app.get_prompt()
    async def get_prompt(name, arguments):
        return types.GetPromptResult(description="", messages=[])

    # Resources (empty but registered for compat)
    @app.list_resources()
    async def list_resources():
        return []

    @app.list_resource_templates()
    async def list_resource_templates():
        return []

    @app.read_resource()
    async def read_resource(uri):
        return []

    return app


def create_server(host=DEFAULT_HOST, port=DEFAULT_PORT, auth_token=None, anchor=False):
    """Create the MCP Server for stdio mode (backward compat with tests)."""
    return _run_stdio_server(host=host, port=port, auth_token=auth_token, anchor=anchor)


# Legacy exports for backward compat
__all__ = ["TDClient", "create_server", "main"]


# ---------------------------------------------------------------------------
# Streamable HTTP server
# ---------------------------------------------------------------------------
def _run_http_server(host=DEFAULT_HTTP_HOST, port=DEFAULT_HTTP_PORT):
    global _client
    _client = TDClient()
    _cleanup_sessions()
    # Start background session cleanup
    def cleanup_loop():
        while True:
            time.sleep(SESSION_CLEANUP_INTERVAL)
            _cleanup_sessions()
    threading.Thread(target=cleanup_loop, daemon=True).start()

    server = HTTPServer((host, port), MCPStreamableHandler)
    print(f"td-mcp-live Streamable HTTP on http://{host}:{port}")
    print("  POST / (JSON-RPC)  |  GET / (SSE)  |  Mcp-Session-Id header for sessions")
    server.serve_forever()


# ---------------------------------------------------------------------------
# Legacy CLI (typed commands against the bridge)
# ---------------------------------------------------------------------------
def _main_legacy():
    ap = argparse.ArgumentParser(prog="td-mcp-live", description="td-mcp live bridge client")
    ap.add_argument("--host", default=DEFAULT_HOST, help="bridge host")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="bridge port")
    ap.add_argument("--anchor", action="store_true",
                    help="attach a doc 'shot' (RAG context) to each tool response")
    ap.add_argument("--auth-token", default=None, help="Bearer auth token")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="bridge + project status").add_argument("path", nargs="?", default="/project1")
    p = sub.add_parser("create", help="create a node")
    p.add_argument("path"); p.add_argument("type"); p.add_argument("--name", default=None)
    sub.add_parser("delete", help="delete a node").add_argument("path")
    p = sub.add_parser("set", help="set parameters (JSON object)"); p.add_argument("path"); p.add_argument("params")
    sub.add_parser("get", help="get parameters").add_argument("path")
    sub.add_parser("errors", help="get cook errors").add_argument("path")
    p = sub.add_parser("exec", help="execute python in TD"); p.add_argument("code")
    sub.add_parser("list", help="list child nodes").add_argument("path", nargs="?", default="/project1")
    p = sub.add_parser("batch", help="batch ops (JSON array)"); p.add_argument("ops")
    p = sub.add_parser("read", help="read a CHOP/TOP/DAT"); p.add_argument("path")
    p.add_argument("--kind", choices=["chop", "top", "dat"], default="chop")
    p.add_argument("--channel", default=None); p.add_argument("--samples", type=int, default=10)
    p.add_argument("--rows", type=int, default=10); p.add_argument("--detail", default="brief")
    p = sub.add_parser("scan", help="scan network topology"); p.add_argument("path", nargs="?", default="/project1")
    p.add_argument("--depth", type=int, default=3)
    p = sub.add_parser("find", help="find nodes by name/type"); p.add_argument("path", nargs="?", default="/project1")
    p.add_argument("--query", default=None); p.add_argument("--type", default=None)
    p.add_argument("--depth", type=int, default=4)

    args = ap.parse_args()
    client = TDClient(args.host, args.port, anchor=args.anchor, auth_token=args.auth_token)
    import json as _json

    def _emit(res):
        print(_json.dumps(res, indent=2, default=str))

    cmd = args.cmd
    if cmd == "status":
        _emit(client.project_info())
    elif cmd == "create":
        _emit(client.create_node(args.path, args.type, name=args.name))
    elif cmd == "delete":
        _emit(client.delete_node(args.path))
    elif cmd == "set":
        _emit(client.set_parameters(args.path, _json.loads(args.params)))
    elif cmd == "get":
        _emit(client.get_parameters(args.path))
    elif cmd == "errors":
        _emit(client.get_errors(args.path))
    elif cmd == "exec":
        _emit(client.execute_python(args.code))
    elif cmd == "list":
        _emit(client.list_nodes(args.path))
    elif cmd == "batch":
        _emit(client.batch(_json.loads(args.ops)))
    elif cmd == "read":
        if args.kind == "top":
            _emit(client.read_top(args.path, detail=args.detail))
        elif args.kind == "dat":
            _emit(client.read_dat(args.path, rows=args.rows))
        else:
            _emit(client.read_chop(args.path, channel=args.channel, samples=args.samples))
    elif cmd == "scan":
        _emit(client.scan_network(args.path, depth=args.depth))
    elif cmd == "find":
        _emit(client.find_nodes(args.path, query=args.query, type=args.type, depth=args.depth))


def main():
    if "--http" in sys.argv:
        sys.argv.remove("--http")
        ap = argparse.ArgumentParser(description="td-mcp-live Streamable HTTP server")
        ap.add_argument("--host", default=DEFAULT_HTTP_HOST)
        ap.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT)
        ap.add_argument("--bridge-host", default=DEFAULT_HOST)
        ap.add_argument("--bridge-port", type=int, default=DEFAULT_PORT)
        ap.add_argument("--auth-token", default=None)
        args, _ = ap.parse_known_args()
        global _client
        _client = TDClient(host=args.bridge_host, port=args.bridge_port, auth_token=args.auth_token)
        _run_http_server(host=args.host, port=args.port)
    elif os.environ.get("TD_MCP_MODE") == "mcp" or "--mcp" in sys.argv:
        sys.argv = [a for a in sys.argv if a != "--mcp"]
        _run_stdio_server()
    else:
        _main_legacy()


if __name__ == "__main__":
    main()