"""td-mcp live server: Streamable HTTP MCP server for TouchDesigner.

Implements the MCP Streamable HTTP + stdio spec (stateless, MCP SDK 2.0):
- JSON-RPC over POST (initialize, tools/list, tools/call, prompts/list, resources/list, server/discover)
- Stateless Streamable HTTP via SDK v2 (no Mcp-Session-Id, stateless_http=True)
- DNS-rebind protection (built into SDK for loopback)
- Backward-compatible stdio mode for Claude Desktop / Cursor
- Bearer-token auth via per-request middleware (TD_MCP_AUTH_TOKEN)
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

import urllib.request
import urllib.error

from td_mcp import __version__
from td_mcp.util import _json_safe

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


def _validate_host_header(host_header):
    if not host_header:
        return False
    host = host_header.split(":")[0].lower()
    return host in ALLOWED_HOSTS


_BRIDGE_UPDATE_HINT = (
    "Re-paste bridge/td_mcp_bridge.py into the TouchDesigner Text DAT "
    "(or reinstall the .tox) so the bridge matches this server."
)


def version_mismatch_warning(bridge_version, server_version=None):
    """Warning when the in-TD bridge does not match this server, else None.

    The bridge is installed by pasting a file into a .toe, so it goes stale
    invisibly: the project keeps working until the server calls a tool added
    after the paste, which fails as an unknown tool with no hint that the
    bridge is the reason. quentinR2 #8 (2026-08-18) surfaces it on the first
    call instead.

    ``server_version`` defaults to this package's version, read at call time
    so it tracks the module rather than freezing at import.
    """
    if server_version is None:
        server_version = globals().get("__version__")
    if not server_version or server_version == "0.0.0":
        return None
    if bridge_version == server_version:
        return None
    if bridge_version is None:
        return (f"TouchDesigner bridge version is unknown - it predates version "
                f"stamping and is older than this server (v{server_version}). "
                + _BRIDGE_UPDATE_HINT)
    return (f"TouchDesigner bridge is v{bridge_version} but this server is "
            f"v{server_version}. Tools added since the bridge was installed will "
            f"fail as unknown tools. " + _BRIDGE_UPDATE_HINT)


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
        # Bridge version handshake, resolved once per client and then reused.
        # None = not yet determined (retried on the next call).
        self._bridge_check = None

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
        self._note_bridge_version(tool, result)
        return result

    def _note_bridge_version(self, tool, result):
        """Learn the bridge's version and ride a warning on stale responses.

        describe_td_tools/project_info already carry bridge_version, so the
        common path costs nothing; any other tool triggers one describe call,
        once per client.
        """
        if not isinstance(result, dict):
            return
        if "bridge_version" in result:
            self._bridge_check = {"bridge_version": result.get("bridge_version")}
        elif self._bridge_check is None and result.get("ok"):
            if tool == "describe_td_tools":
                # The handshake call itself answered without a version: this
                # bridge predates stamping. Record that instead of looping.
                self._bridge_check = {"bridge_version": None}
            else:
                probe = self._call("describe_td_tools")
                self._bridge_check = (
                    {"bridge_version": probe.get("bridge_version")}
                    if isinstance(probe, dict) and probe.get("ok") else None
                )
        if self._bridge_check is not None:
            warning = version_mismatch_warning(self._bridge_check["bridge_version"])
            if warning:
                result.setdefault("warning", warning)

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

    def get_parameter_info(self, path, names=None):
        return self._call("get_parameter_info", {"path": path, "names": names})

    def bind_parameter_expression(self, path, param, expression):
        return self._call("bind_parameter_expression",
                          {"path": path, "param": param, "expression": expression})

    def get_errors(self, path, include_warnings=True):
        return self._call("get_errors", {"path": path,
                                         "include_warnings": include_warnings})

    def execute_python(self, code):
        return self._call("execute_python", {"code": code})

    def list_nodes(self, path):
        return self._call("list_nodes", {"path": path})

    def project_info(self):
        return self._call("project_info", {})

    def capture_viewport(self, path, detail="normal"):
        result = self._call("capture_viewport", {"path": path, "detailLevel": detail})
        # Image-block feedback (mintiasaikoh): when the bridge returns a PNG
        # file, attach base64 image content so MCP clients can render it
        # directly instead of only seeing the verdict text.
        if isinstance(result, dict) and result.get("ok") and result.get("file"):
            try:
                with open(result["file"], "rb") as f:
                    import base64
                    b64 = base64.b64encode(f.read()).decode("ascii")
                    result["image"] = {
                        "mime_type": "image/png",
                        "data": b64,
                        "description": result.get("verdict", {}).get("caption",
                                      "TouchDesigner viewport capture"),
                    }
            except Exception:  # noqa: BLE001
                pass
        return result

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

    def set_dat_text(self, path, text, expected_sha256=None):
        return self._call("set_dat_text", {"path": path, "text": text,
                                           "expected_sha256": expected_sha256})

    def scan_network(self, path, depth=3):
        return self._call("scan_network", {"path": path, "depth": depth})

    def build_and_verify(self, path, op_type, params=None):
        """Create → set parameters → errors → viewport verdict → offline heal.

        The last hop is the offline↔live integration point (§3.1): after
        capturing the viewport, the client asks the offline orchestrator to
        validate/score the *created network* with the viewport verdict folded
        in — so a flat render triggers a low score and self-heal candidates
        instead of being silently accepted.
        """
        created = self.create_node(path, op_type)
        if not created.get("ok"):
            return created
        node_path = created.get("path")
        if params:
            self.set_parameters(node_path, params)
        errs = self.get_errors(node_path)
        verdict = self.capture_viewport(node_path)
        offline = None
        try:
            from td_mcp.server_offline import td_validate_live
            from td_mcp import tdn as tdn_mod
            snapshot = tdn_mod.new_network(
                path,
                operators=[tdn_mod.operator(
                    node_path.rsplit("/", 1)[-1], op_type,
                    parameters=params or {})])
            offline = td_validate_live(
                tdn_mod.export_network(snapshot),
                viewport_verdict=verdict)
        except Exception:  # noqa: BLE001
            offline = None
        out = {"created": created, "errors": errs, "preview": verdict}
        if offline:
            out["offline_validation"] = offline
        return out

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

    def get_non_default_params(self, path):
        """Token-efficient read (vedant1317): non-default params only."""
        result = self.get_node(path)
        if not result.get("ok"):
            return result
        params = result.get("params", {})
        return {"ok": True, "path": path,
                "non_default_count": len(params),
                "non_default_params": params}

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
    "get_parameter_info": _wrap_tool(lambda c, a: c.get_parameter_info(a.get("path"), a.get("names"))),
    "bind_parameter_expression": _wrap_tool(lambda c, a: c.bind_parameter_expression(a.get("path"), a.get("param"), a.get("expression"))),
    "get_errors": _wrap_tool(lambda c, a: c.get_errors(
        a.get("path"), a.get("include_warnings", True))),
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
    "set_dat_text": _wrap_tool(lambda c, a: c.set_dat_text(a.get("path"), a.get("text", ""), a.get("expected_sha256"))),
    "scan_network": _wrap_tool(lambda c, a: c.scan_network(a.get("path"), a.get("depth", 3))),
    "build_and_verify": _wrap_tool(lambda c, a: c.build_and_verify(a.get("path"), a.get("op_type"), a.get("params"))),
    "connect_nodes": _wrap_tool(lambda c, a: c.connect_nodes(a.get("from_path"), a.get("to_path"),
                                                              a.get("from_output", 0), a.get("to_input", 0))),
    "rename_node": _wrap_tool(lambda c, a: c.rename_node(a.get("path"), a.get("new_name"))),
    "copy_node": _wrap_tool(lambda c, a: c.copy_node(a.get("path"), a.get("new_parent"), a.get("new_name"))),
    "auto_layout": _wrap_tool(lambda c, a: c.auto_layout(a.get("path"), a.get("direction", "left-right"),
                                                          a.get("spacing_x", 200), a.get("spacing_y", 200))),
    "get_node": _wrap_tool(lambda c, a: c.get_node(a.get("path"))),
    "get_non_default_params": _wrap_tool(lambda c, a: c.get_non_default_params(a.get("path"))),
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
    ("get_parameter_info", "Full per-parameter schema: label/value/mode/default/page/style/bounds/menu.", {"path": {"type": "string"}, "names": {"type": "string"}}),
    ("bind_parameter_expression", "Bind a parameter expression with eval-error reporting.", {"path": {"type": "string"}, "param": {"type": "string"}, "expression": {"type": "string"}}),
    ("get_errors", "Get errors and warnings under a node, split by severity "
     "(error_count / warning_count); a warning is not a failure.",
     {"path": {"type": "string"},
      "include_warnings": {"type": "boolean"}}),
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
    ("set_dat_text", "Set a DAT's text with optional SHA-256 optimistic concurrency guard.", {"path": {"type": "string"}, "text": {"type": "string"}, "expected_sha256": {"type": "string"}}),
    ("scan_network", "Scan network topology.", {"path": {"type": "string"}, "depth": {"type": "integer"}}),
    ("build_and_verify", "Create->verify->preview loop.", {"path": {"type": "string"}, "op_type": {"type": "string"}, "params": {"type": "object"}}),
    ("connect_nodes", "Wire nodes.", {"from_path": {"type": "string"}, "to_path": {"type": "string"}, "from_output": {"type": "integer"}, "to_input": {"type": "integer"}}),
    ("rename_node", "Rename node.", {"path": {"type": "string"}, "new_name": {"type": "string"}}),
    ("copy_node", "Copy node.", {"path": {"type": "string"}, "new_parent": {"type": "string"}, "new_name": {"type": "string"}}),
    ("auto_layout", "Auto-arrange nodes.", {"path": {"type": "string"}, "direction": {"type": "string"}, "spacing_x": {"type": "integer"}, "spacing_y": {"type": "integer"}}),
    ("get_node", "Get detailed node info.", {"path": {"type": "string"}}),
    ("get_non_default_params", "Token-efficient read: non-default params only.", {"path": {"type": "string"}}),
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
async def _live_list_tools(ctx, params):
    """List all bridge tools with v2 cache hints."""
    from td_mcp.tools.risk import tool_annotations
    return types.ListToolsResult(
        tools=[
            types.Tool(name=name, description=desc,
                       inputSchema={"type": "object", "properties": props},
                       annotations=tool_annotations(name))
            for name, desc, props in _TOOL_META
        ],
        ttl_ms=60000,
        cache_scope="private",
    )


async def _live_call_tool(ctx, params):
    """Dispatch a tool call to the bridge. Errors are returned as
    CallToolResult(isError=True) — never raised into the transport."""
    a = params.arguments or {}
    try:
        fn = TOOL_REGISTRY.get(params.name)
        if fn is None:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"unknown tool: {params.name}")],
                is_error=True,
            )
        result = fn(a)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(_json_safe(result), indent=2, default=_json_safe))],
            result_type="complete",
        )
    except Exception as e:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"error: {e}")],
            is_error=True,
        )


async def _live_list_prompts(ctx, params):
    from td_mcp import prompts as prompts_mod
    prompts = []
    for pname in prompts_mod.list_experts():
        prompts.append(types.Prompt(name=pname, description=f"Expert persona: {pname}",
                                    arguments=[]))
    for phase in ("plan", "build", "self_improve"):
        prompts.append(types.Prompt(
            name=f"phase_{phase}",
            description=f"Combined system prompt for the '{phase}' build phase.",
            arguments=[]))
    return types.ListPromptsResult(prompts=prompts, ttl_ms=60000, cache_scope="private")


async def _live_get_prompt(ctx, params):
    from td_mcp import prompts as prompts_mod
    name = params.name
    if name in prompts_mod.list_experts():
        text = prompts_mod.get_expert_prompt(name) or ""
    elif name.startswith("phase_"):
        text = prompts_mod.build_phase_prompt(name[len("phase_"):])
    else:
        text = prompts_mod.build_phase_prompt("build")
    return types.GetPromptResult(
        description=name,
        messages=[types.PromptMessage(role="user",
                   content=types.TextContent(type="text", text=text))])


async def _live_list_resources(ctx, params):
    return types.ListResourcesResult(resources=[], ttl_ms=60000, cache_scope="private")


async def _live_read_resource(ctx, params):
    return types.ReadResourceResult(contents=[])


# ---------------------------------------------------------------------------
# MCP stdio server (for Claude Desktop / Cursor)
# ---------------------------------------------------------------------------
def _run_stdio_server(auth_token=None, anchor=False):
    """Build the MCP Server for stdio mode (Claude Desktop / Cursor)."""
    if not MCP_AVAILABLE:
        print("mcp package not installed. pip install mcp", file=sys.stderr)
        sys.exit(1)

    app = Server(
        name="td-mcp-live",
        version=__version__,
        on_list_tools=_live_list_tools,
        on_call_tool=_live_call_tool,
        on_list_resources=_live_list_resources,
        on_read_resource=_live_read_resource,
        on_list_prompts=_live_list_prompts,
        on_get_prompt=_live_get_prompt,
    )
    _get_client()
    return app

def create_server(host=DEFAULT_HOST, port=DEFAULT_PORT, auth_token=None, anchor=False):
    """Create the MCP Server for stdio mode (backward compat with tests)."""
    return _run_stdio_server(auth_token=auth_token, anchor=anchor)


# Legacy exports for backward compat
__all__ = ["TDClient", "create_server", "main", "_BearerAuthMiddleware"]


# ---------------------------------------------------------------------------
# Streamable HTTP server
# ---------------------------------------------------------------------------
class _BearerAuthMiddleware:
    """ASGI middleware: require a Bearer token on every request (stateless).

    The token is passed via TD_MCP_AUTH_TOKEN at startup and compared with
    hmac.compare_digest to avoid timing leaks.  No session store is needed —
    every request is independently authenticated.
    """

    def __init__(self, app, auth_token):
        self.app = app
        self._expected = f"Bearer {auth_token}".encode() if auth_token else None

    async def __call__(self, scope, receive, send):
        if self._expected is None or scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers", [])
        auth = b""
        for k, v in headers:
            if k == b"authorization":
                auth = v
                break
        if not hmac.compare_digest(auth, self._expected):
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body",
                        "body": json.dumps({"error": "Unauthorized"}).encode()})
            return
        await self.app(scope, receive, send)


def _run_http_server(host=DEFAULT_HTTP_HOST, port=DEFAULT_HTTP_PORT, auth_token=None):
    """Run the stateless Streamable HTTP MCP server via uvicorn (SDK 2.0).

    Replaces the old custom BaseHTTPRequestHandler + Mcp-Session-Id session
    store with the SDK's built-in stateless transport.  DNS-rebinding
    protection and server/discover are handled by the SDK automatically.
    """
    _get_client()
    server = create_server()
    app = server.streamable_http_app(stateless_http=True)
    if auth_token:
        app = _BearerAuthMiddleware(app, auth_token)
    print(f"td-mcp-live Streamable HTTP (stateless) on http://{host}:{port}")
    print("  POST /mcp  |  GET /mcp -> 405  |  server/discover on POST /mcp")
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")

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
        _run_http_server(host=args.host, port=args.port, auth_token=args.auth_token)
    elif os.environ.get("TD_MCP_MODE") == "mcp" or "--mcp" in sys.argv:
        sys.argv = [a for a in sys.argv if a != "--mcp"]
        app = _run_stdio_server()
        from mcp.server.stdio import stdio_server
        import anyio

        async def _serve_stdio():
            async with stdio_server() as (read_stream, write_stream):
                await app.run(
                    read_stream, write_stream, app.create_initialization_options()
                )

        anyio.run(_serve_stdio)
    else:
        _main_legacy()


if __name__ == "__main__":
    main()