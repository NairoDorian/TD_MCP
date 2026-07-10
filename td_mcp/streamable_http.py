"""Streamable HTTP transport for the td-mcp bridge (8beeeaaat / TD_Builder_alpha style).

Features:
- MCP JSON-RPC over HTTP (initialize, tools/call, tools/list, etc.)
- Server-Sent Events (SSE) for server→client streaming
- Multi-session support with `Mcp-Session-Id` header
- DNS-rebind protection (Host header must be localhost)
- Constant-time auth compare
- Backward compatible with existing /mcp POST
"""

import json
import os
import uuid
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import hmac

try:
    from td_mcp import __version__
except Exception:  # bridge may run where the package metadata is unavailable
    __version__ = "0.0.0"

# Session management
_sessions = {}  # session_id -> {"created": float, "last_seen": float, "auth_token": str}
_sessions_lock = threading.Lock()

SESSION_TTL = 3600  # 1 hour
SESSION_CLEANUP_INTERVAL = 300  # 5 min


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


def _validate_host_header(host_header, allowed_hosts=None):
    """DNS-rebind guard: ensure Host is localhost/127.0.0.1 only."""
    if not host_header:
        return False
    host = host_header.split(":")[0].lower()
    allowed = allowed_hosts or {"localhost", "127.0.0.1", "::1"}
    return host in allowed


# ---------------------------------------------------------------------------
# Streamable HTTP handler mixin
# ---------------------------------------------------------------------------
class StreamableHTTPMixin:
    """
    Mixin for BaseHTTPRequestHandler to add MCP Streamable HTTP support.
    Usage: class MCPHandler(StreamableHTTPMixin, BaseHTTPRequestHandler): ...
    
    Requires the following attributes on the handler:
    - AUTH_TOKEN (str)
    - DISPATCH (dict: tool_name -> callable)
    - _wrap (callable)
    - _ws_push (callable)  # optional, for push events
    """

    # Override these in subclass
    AUTH_TOKEN = ""
    DISPATCH = {}
    _wrap = staticmethod(lambda x: x)
    _ws_push = staticmethod(lambda e: None)

    # Session management
    def _get_or_create_session(self):
        """Get existing session or create new one from Mcp-Session-Id header."""
        session_id = self.headers.get("Mcp-Session-Id")
        if session_id:
            session = _get_session(session_id)
            if session:
                return session_id, session
        # Create new
        session_id = _generate_session_id()
        session = _touch_session(session_id, self.AUTH_TOKEN)
        return session_id, session

    def _check_dns_rebind(self):
        """Reject requests with non-localhost Host header (DNS-rebind protection)."""
        host = self.headers.get("Host", "")
        if not _validate_host_header(host):
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"jsonrpc": "2.0", "error": {"code": -32600, "message": "Forbidden: DNS-rebind protection"}}).encode())
            return False
        return True

    def _auth_ok(self, session):
        """Constant-time auth check using session's token."""
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {session.get('auth_token', self.AUTH_TOKEN)}"
        return hmac.compare_digest(supplied, expected)

    def _send_sse(self, data, event=None):
        """Write an SSE frame."""
        out = []
        if event:
            out.append(f"event: {event}")
        out.append(f"data: {json.dumps(data)}")
        out.append("")  # empty line terminates event
        frame = "\n".join(out) + "\n"
        self.wfile.write(frame.encode("utf-8"))
        self.wfile.flush()

    def _send_jsonrpc_response(self, request_id, result=None, error=None):
        """Send a JSON-RPC 2.0 response."""
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

    def _handle_jsonrpc(self, payload, session_id, session):
        """Process a JSON-RPC request payload (dict or list)."""
        requests = payload if isinstance(payload, list) else [payload]
        responses = []

        for req in requests:
            req_id = req.get("id")
            method = req.get("method")
            params = req.get("params", {})

            if method == "initialize":
                # MCP initialize - return server capabilities
                responses.append({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {},
                            "prompts": {},
                            "resources": {},
                            "logging": {}
                        },
                        "serverInfo": {"name": "td-mcp-bridge", "version": __version__}
                    }
                })
            elif method == "notifications/initialized":
                # Client acknowledges initialize - no response
                pass
            elif method == "tools/list":
                tool_list = []
                for name, fn in self.DISPATCH.items():
                    # Minimal schema - extend as needed
                    tool_list.append({
                        "name": name,
                        "description": fn.__doc__ or f"Tool {name}",
                        "inputSchema": {"type": "object", "properties": {}}
                    })
                responses.append({"jsonrpc": "2.0", "id": req_id, "result": {"tools": tool_list}})
            elif method == "tools/call":
                tool_name = params.get("name")
                args = params.get("arguments", {})
                fn = self.DISPATCH.get(tool_name)
                if fn is None:
                    responses.append({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
                    })
                else:
                    try:
                        # ui.undo block is applied by the tool itself in our bridge
                        result = self._wrap(fn(args))
                        responses.append({
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "content": [{"type": "text", "text": json.dumps(result)}],
                                "isError": result.get("ok") is False
                            }
                        })
                        # Push event for WS clients
                        ok = result.get("ok") if isinstance(result, dict) else True
                        self._ws_push({"tool": tool_name, "ok": ok})
                    except Exception as e:
                        responses.append({
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {"code": -32603, "message": str(e)}
                        })
            elif method == "prompts/list":
                responses.append({"jsonrpc": "2.0", "id": req_id, "result": {"prompts": []}})
            elif method == "resources/list":
                responses.append({"jsonrpc": "2.0", "id": req_id, "result": {"resources": []}})
            elif method == "resources/templates/list":
                responses.append({"jsonrpc": "2.0", "id": req_id, "result": {"resourceTemplates": []}})
            else:
                responses.append({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}
                })

        # For batch requests, return array; for single, return single
        if isinstance(payload, list):
            return responses
        return responses[0] if responses else None

    def do_POST(self):
        if not self._check_dns_rebind():
            return

        session_id, session = self._get_or_create_session()
        self._current_session_id = session_id

        if not self._auth_ok(session):
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"jsonrpc": "2.0", "error": {"code": -32600, "message": "Unauthorized"}}).encode())
            return

        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            self.send_response(415)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        response = self._handle_jsonrpc(payload, session_id, session)

        # Check if client wants SSE streaming (Accept: text/event-stream)
        accept = self.headers.get("Accept", "")
        if "text/event-stream" in accept:
            self._send_sse_response(response, session_id)
        else:
            self._send_standard_response(response, session_id)

    def _send_standard_response(self, response, session_id):
        """Send standard JSON-RPC response."""
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", session_id)
        self.end_headers()
        self.wfile.write(body)

    def _send_sse_response(self, response, session_id):
        """Send response as SSE stream (single event + done)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Mcp-Session-Id", session_id)
        self.end_headers()
        # Single event with the response
        self._send_sse(response, event="message")
        # End stream
        self._send_sse({"type": "done"}, event="done")

    def do_GET(self):
        """Handle SSE connections for server→client streaming."""
        if not self._check_dns_rebind():
            return

        # Check for SSE request
        accept = self.headers.get("Accept", "")
        if "text/event-stream" not in accept:
            self.send_response(406)
            self.end_headers()
            return

        session_id = self.headers.get("Mcp-Session-Id")
        if not session_id:
            session_id, _ = self._get_or_create_session()
        session = _get_session(session_id)
        if not session:
            self.send_response(404)
            self.end_headers()
            return

        if not self._auth_ok(session):
            self.send_response(401)
            self.end_headers()
            return

        # Upgrade to SSE
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Mcp-Session-Id", session_id)
        self.end_headers()

        # Send initial connection event
        self._send_sse({"type": "connected", "session": session_id}, event="open")

        # Keep connection alive - in a real implementation you'd register
        # this client for push events. For now, just send heartbeat.
        try:
            while True:
                time.sleep(30)
                self._send_sse({"type": "heartbeat", "ts": time.time()}, event="heartbeat")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self):
        """CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, Mcp-Session-Id")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()

    def log_message(self, *args):
        pass


# ---------------------------------------------------------------------------
# Backward-compatible bridge handler (for existing /mcp POST clients)
# ---------------------------------------------------------------------------
def make_legacy_handler(auth_token, dispatch, wrap_fn, ws_push_fn):
    """Create a handler that wraps the new StreamableHTTPMixin but exposes
    the legacy /mcp endpoint for existing clients."""
    
    class LegacyHandler(StreamableHTTPMixin, BaseHTTPRequestHandler):
        AUTH_TOKEN = auth_token
        DISPATCH = dispatch
        _wrap = staticmethod(wrap_fn)
        _ws_push = staticmethod(ws_push_fn)

        def do_POST(self):
            # Legacy path /mcp - still support old clients
            if self.path == "/mcp":
                # Delegate to StreamableHTTPMixin's do_POST which handles JSON-RPC
                return super().do_POST()
            # Fall through to original handler if defined
            self.send_response(404)
            self.end_headers()

        def do_GET(self):
            if self.path == "/api/status":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "status": "running"}).encode())
            elif self.path.startswith("/api/resource"):
                # Existing resource endpoint
                super().do_GET()
            else:
                super().do_GET()

    return LegacyHandler


# ---------------------------------------------------------------------------
# Background session cleanup
# ---------------------------------------------------------------------------
_cleanup_thread = None

def start_session_cleanup():
    global _cleanup_thread
    def runner():
        while True:
            time.sleep(SESSION_CLEANUP_INTERVAL)
            _cleanup_sessions()
    _cleanup_thread = threading.Thread(target=runner, daemon=True)
    _cleanup_thread.start()

def stop_session_cleanup():
    pass  # daemon thread exits on process end