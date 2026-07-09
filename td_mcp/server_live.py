"""td-mcp live client: talks to the TouchDesigner bridge (.tox) over HTTP.

The bridge runs inside TD (see bridge/td_mcp_bridge.py) and listens on
http://127.0.0.1:9980. Every mutation is wrapped in ui.undo on the
TD side, so one Ctrl+Z reverts a whole agent batch.

CLI:  python -m td_mcp.server_live create /project1 CircleTOP TOP
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
import threading

try:
    import websocket
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9980


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
        req = urllib.request.Request(
            f"{self.base}/mcp", data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                result = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        if self.anchor and isinstance(result, dict) and result.get("ok"):
            result["shots"] = self._anchor(tool, args or {})
        return result

    def status(self):
        try:
            with urllib.request.urlopen(f"{self.base}/api/status", timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

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

    def scan_network(self, path, depth=3):
        return self._call("scan_network", {"path": path, "depth": depth})

    def describe_td_tools(self):
        return self._call("describe_td_tools", {})

    def batch(self, ops):
        """Collapse several tool calls into one round-trip."""
        return self._call("batch", {"ops": ops})

    def read_chop(self, path, channel=None, samples=10):
        return self._call("read_chop", {"path": path, "channel": channel, "samples": samples})

    def read_top(self, path, detail="brief"):
        return self._call("read_top", {"path": path, "detailLevel": detail})

    def read_dat(self, path, rows=10):
        return self._call("read_dat", {"path": path, "rows": rows})

    def build_and_verify(self, path, op_type, params=None):
        """Create -> verify (errors) -> preview (viewport verdict) loop
        (tdmcp / johnsabath / Embody). Returns the create result, any errors,
        and a viewport quality verdict so an agent knows if output is empty."""
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
        args = {"path": path, "new_parent": new_parent}
        if new_name is not None:
            args["new_name"] = new_name
        return self._call("copy_node", args)

    def auto_layout(self, path, direction="left-right", spacing_x=200, spacing_y=200):
        return self._call("auto_layout", {"path": path, "direction": direction,
                                          "spacing_x": spacing_x, "spacing_y": spacing_y})

    def get_node(self, path):
        return self._call("get_node", {"path": path})

    def set_node_color(self, path, r, g, b):
        return self._call("set_node_color", {"path": path, "r": r, "g": g, "b": b})

    def set_node_comment(self, path, comment=None, tags=None):
        args = {"path": path}
        if comment is not None:
            args["comment"] = comment
        if tags is not None:
            args["tags"] = tags
        return self._call("set_node_comment", args)

    def map_network(self, path, depth=2):
        return self._call("map_network", {"path": path, "depth": depth})

    def save_tox(self, path, file_path=None):
        args = {"path": path}
        if file_path is not None:
            args["file_path"] = file_path
        return self._call("save_tox", args)

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

    def find_nodes(self, path, query=None, op_type=None, depth=4):
        return self._call("find_nodes", {"path": path, "query": query,
                                         "type": op_type, "depth": depth})

    def set_node_position(self, path, x, y):
        return self._call("set_node_position", {"path": path, "x": x, "y": y})

    def timeline(self, action, value=None):
        return self._call("timeline", {"action": action, "value": value})

    def export_recipe(self, path, depth=3):
        return self._call("export_recipe", {"path": path, "depth": depth})

    def import_recipe(self, recipe, target_parent="*here"):
        return self._call("import_recipe", {"recipe": recipe, "target_parent": target_parent})


# ---------------------------------------------------------------------------
# WebSocket transport (benoitliard / touch_mcp style)
# ---------------------------------------------------------------------------
class WSClient:
    """WebSocket client for the TD bridge (fallback if websocket-client not installed)."""

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=30,
                 auth_token=None):
        if not WS_AVAILABLE:
            raise RuntimeError("websocket-client not installed: pip install websocket-client")
        self.url = f"ws://{host}:{port}/ws"
        self.timeout = timeout
        self.auth_token = auth_token or os.environ.get("TD_MCP_AUTH_TOKEN")
        self._ws = None
        self._msg_id = 0
        self._pending = {}

    def connect(self):
        import websocket
        self._ws = websocket.create_connection(self.url, timeout=self.timeout)
        if self.auth_token:
            self._ws.send(json.dumps({"type": "auth", "token": self.auth_token}))
            resp = json.loads(self._ws.recv())
            if not resp.get("ok"):
                raise RuntimeError(f"Auth failed: {resp.get('error')}")

    def close(self):
        if self._ws:
            self._ws.close()
            self._ws = None

    def _call(self, tool, args=None):
        if not self._ws:
            self.connect()
        self._msg_id += 1
        msg = {"id": self._msg_id, "tool": tool, "args": args or {}}
        self._ws.send(json.dumps(msg))
        while True:
            raw = self._ws.recv()
            if not raw:
                continue
            resp = json.loads(raw)
            if resp.get("id") == self._msg_id:
                if resp.get("ok"):
                    return resp.get("result", {"ok": True})
                return {"ok": False, "error": resp.get("error")}
            # handle async notifications if any

    def __getattr__(self, name):
        # Delegate to _call for any tool method
        def method(*args, **kwargs):
            return self._call(name, kwargs or (args[0] if args else {}))
        return method

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()


def create_server(host=DEFAULT_HOST, port=DEFAULT_PORT, auth_token=None, anchor=False):
    from mcp.server import Server
    import mcp.types as types

    app = Server("td-mcp-live")
    client = TDClient(host=host, port=port, auth_token=auth_token, anchor=anchor)

    # Risk tiers (TrueFiasco)
    read_only = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
    modifying = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
    destructive = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False}

    @app.list_tools()
    async def list_tools():
        return [
            types.Tool("create_node", "Create a TouchDesigner node of a specific type.",
                       {"path": {"type": "string"}, "type": {"type": "string"}, "name": {"type": "string", "optional": True}},
                       annotations=modifying),
            types.Tool("delete_node", "Delete a TouchDesigner node at a path.",
                       {"path": {"type": "string"}},
                       annotations=destructive),
            types.Tool("set_parameters", "Set parameters for a node at a path. A param value may be a dict: {\"expr\": \"...\"} to set an expression, or {\"pulse\": true} to pulse a button.",
                        {"path": {"type": "string"}, "params": {"type": "object"}},
                        annotations=modifying),
            types.Tool("get_parameters", "Get parameters for a node at a path.",
                       {"path": {"type": "string"}},
                       annotations=read_only),
            types.Tool("get_errors", "Get error messages for a node at a path.",
                       {"path": {"type": "string"}},
                       annotations=read_only),
            types.Tool("execute_python", "Execute python code inside TouchDesigner.",
                       {"code": {"type": "string"}},
                       annotations=destructive),
            types.Tool("list_nodes", "List child nodes of a parent path.",
                       {"path": {"type": "string"}},
                       annotations=read_only),
            types.Tool("project_info", "Get TouchDesigner project information.",
                       {},
                       annotations=read_only),
            types.Tool("capture_viewport", "Capture node viewport and return image path plus is_black/is_flat verdict.",
                       {"path": {"type": "string"}, "detail": {"type": "string", "optional": True}},
                       annotations=read_only),
            types.Tool("get_resource", "Read a live resource (e.g. td://chop/path).",
                       {"uri": {"type": "string"}},
                       annotations=read_only),
            types.Tool("describe_td_tools", "Get bridge capabilities and authorized paths.",
                       {},
                       annotations=read_only),
            types.Tool("batch", "Execute multiple bridge operations in a single round-trip.",
                       {"ops": {"type": "array"}},
                       annotations=modifying),
            types.Tool("read_chop", "Read sample values from a CHOP.",
                       {"path": {"type": "string"}, "channel": {"type": "string", "optional": True}, "samples": {"type": "integer", "optional": True}},
                       annotations=read_only),
            types.Tool("read_top", "Read resolution and metadata from a TOP.",
                       {"path": {"type": "string"}, "detail": {"type": "string", "optional": True}},
                       annotations=read_only),
            types.Tool("read_dat", "Read rows of data from a DAT.",
                       {"path": {"type": "string"}, "rows": {"type": "integer", "optional": True}},
                       annotations=read_only),
            types.Tool("scan_network", "Recursively scan the TouchDesigner network topology, collecting connections and parameters.",
                       {"path": {"type": "string"}, "depth": {"type": "integer", "optional": True}},
                       annotations=read_only),
            types.Tool("build_and_verify", "Create -> verify (errors) -> preview (viewport verdict) loop for a node.",
                        {"path": {"type": "string"}, "op_type": {"type": "string"}, "params": {"type": "object", "optional": True}},
                        annotations=modifying),
            types.Tool("connect_nodes", "Wire one node's output into another node's input.",
                        {"from_path": {"type": "string"}, "to_path": {"type": "string"},
                         "from_output": {"type": "integer", "optional": True}, "to_input": {"type": "integer", "optional": True}},
                        annotations=modifying),
            types.Tool("rename_node", "Rename a TouchDesigner node.",
                        {"path": {"type": "string"}, "new_name": {"type": "string"}},
                        annotations=modifying),
            types.Tool("copy_node", "Copy a node into another parent COMP.",
                        {"path": {"type": "string"}, "new_parent": {"type": "string"}, "new_name": {"type": "string", "optional": True}},
                        annotations=modifying),
            types.Tool("auto_layout", "Auto-arrange all child nodes of a COMP in a clean grid.",
                        {"path": {"type": "string"}, "direction": {"type": "string", "optional": True},
                         "spacing_x": {"type": "integer", "optional": True}, "spacing_y": {"type": "integer", "optional": True}},
                        annotations=modifying),
            types.Tool("get_node", "Get detailed info about one node: type, path, connections, non-default params, errors, position.",
                        {"path": {"type": "string"}},
                        annotations=read_only),
            types.Tool("set_node_color", "Set the display color of a node (RGB 0..1) to organize the network.",
                        {"path": {"type": "string"}, "r": {"type": "number"}, "g": {"type": "number"}, "b": {"type": "number"}},
                        annotations=modifying),
            types.Tool("set_node_comment", "Annotate a node with a comment and/or tags (list or comma string) for documentation.",
                        {"path": {"type": "string"}, "comment": {"type": "string", "optional": True}, "tags": {"type": "string", "optional": True}},
                        annotations=modifying),
            types.Tool("map_network", "Emit a Graphviz DOT graph of the network: connections, positions, topology. Render with `dot -Tpng`.",
                        {"path": {"type": "string"}, "depth": {"type": "integer", "optional": True}},
                        annotations=read_only),
            types.Tool("disconnect_nodes", "Break the wire from one node's output into another node's input.",
                        {"from_path": {"type": "string"}, "to_path": {"type": "string"}, "to_input": {"type": "integer", "optional": True}},
                        annotations=modifying),
            types.Tool("get_connections", "Return a normalized wiring map (inputs + outputs) for a node.",
                        {"path": {"type": "string"}},
                        annotations=read_only),
            types.Tool("exec_node_method", "Call a method on a node (e.g. cook, reset).",
                        {"path": {"type": "string"}, "method": {"type": "string"}, "args": {"type": "array", "optional": True}},
                        annotations=modifying),
            types.Tool("snapshot_network", "Durable checkpoint: save a COMP to a temp .tox that survives the undo stack.",
                        {"path": {"type": "string"}},
                        annotations=modifying),
            types.Tool("restore_network", "Restore a checkpoint .tox (from snapshot_network) into a parent COMP.",
                        {"snapshot": {"type": "string"}, "target_parent": {"type": "string", "optional": True}},
                        annotations=modifying),
            types.Tool("get_performance", "Rank child cook times (CPU/GPU) to surface performance hotspots.",
                        {"path": {"type": "string"}},
                        annotations=read_only),
            types.Tool("validate_network", "Scene-contract check: flags unplaced/overlapping nodes and cook errors.",
                        {"path": {"type": "string"}, "depth": {"type": "integer", "optional": True}},
                        annotations=read_only),
            types.Tool("set_flags", "Toggle node flags (bypass, viewer, excludeFromCook, allowCooking, forceCooking, cloneImmune, pickable).",
                        {"path": {"type": "string"}, "flags": {"type": "object"}},
                        annotations=modifying),
            types.Tool("find_nodes", "Search descendants by name substring and/or operator type.",
                        {"path": {"type": "string"}, "query": {"type": "string", "optional": True},
                         "type": {"type": "string", "optional": True}, "depth": {"type": "integer", "optional": True}},
                        annotations=read_only),
            types.Tool("set_node_position", "Move a single node to an explicit grid position (x, y).",
                        {"path": {"type": "string"}, "x": {"type": "integer"}, "y": {"type": "integer"}},
                        annotations=modifying),
            types.Tool("timeline", "Control the global timeline: play / pause / toggle / frame / seek / rate.",
                        {"action": {"type": "string"}, "value": {"type": "number", "optional": True}},
                        annotations=modifying),
            types.Tool("export_recipe", "Serialize a subnetwork into a portable JSON blueprint (repeatable builds).",
                        {"path": {"type": "string"}, "depth": {"type": "integer", "optional": True}},
                        annotations=read_only),
            types.Tool("import_recipe", "Rebuild a blueprint (from export_recipe) under a parent COMP.",
                        {"recipe": {"type": "object"}, "target_parent": {"type": "string", "optional": True}},
                        annotations=modifying),
            types.Tool("save_tox", "Save a COMP operator as a reusable .tox component file.",
                        {"path": {"type": "string"}, "file_path": {"type": "string", "optional": True}},
                        annotations=modifying),
        ]

    @app.call_tool()
    async def call_tool(name, arguments):
        a = arguments or {}
        try:
            if name == "create_node":
                res = client.create_node(a.get("path"), a.get("type"), a.get("name"))
            elif name == "delete_node":
                res = client.delete_node(a.get("path"))
            elif name == "set_parameters":
                res = client.set_parameters(a.get("path"), a.get("params", {}))
            elif name == "get_parameters":
                res = client.get_parameters(a.get("path"))
            elif name == "get_errors":
                res = client.get_errors(a.get("path"))
            elif name == "execute_python":
                res = client.execute_python(a.get("code"))
            elif name == "list_nodes":
                res = client.list_nodes(a.get("path"))
            elif name == "project_info":
                res = client.project_info()
            elif name == "capture_viewport":
                res = client.capture_viewport(a.get("path"), a.get("detail", "normal"))
            elif name == "get_resource":
                res = client.get_resource(a.get("uri"))
            elif name == "describe_td_tools":
                res = client.describe_td_tools()
            elif name == "batch":
                res = client.batch(a.get("ops", []))
            elif name == "read_chop":
                res = client.read_chop(a.get("path"), a.get("channel"), a.get("samples", 10))
            elif name == "read_top":
                res = client.read_top(a.get("path"), a.get("detail", "brief"))
            elif name == "read_dat":
                res = client.read_dat(a.get("path"), a.get("rows", 10))
            elif name == "scan_network":
                res = client.scan_network(a.get("path"), a.get("depth", 3))
            elif name == "build_and_verify":
                res = client.build_and_verify(a.get("path"), a.get("op_type"), a.get("params"))
            elif name == "connect_nodes":
                res = client.connect_nodes(a.get("from_path"), a.get("to_path"),
                                           a.get("from_output", 0), a.get("to_input", 0))
            elif name == "rename_node":
                res = client.rename_node(a.get("path"), a.get("new_name"))
            elif name == "copy_node":
                res = client.copy_node(a.get("path"), a.get("new_parent"), a.get("new_name"))
            elif name == "auto_layout":
                res = client.auto_layout(a.get("path"), a.get("direction", "left-right"),
                                         a.get("spacing_x", 200), a.get("spacing_y", 200))
            elif name == "get_node":
                res = client.get_node(a.get("path"))
            elif name == "set_node_color":
                res = client.set_node_color(a.get("path"), a.get("r", 0), a.get("g", 0), a.get("b", 0))
            elif name == "set_node_comment":
                res = client.set_node_comment(a.get("path"), a.get("comment"), a.get("tags"))
            elif name == "map_network":
                res = client.map_network(a.get("path"), a.get("depth", 2))
            elif name == "disconnect_nodes":
                res = client.disconnect_nodes(a.get("from_path"), a.get("to_path"), a.get("to_input", 0))
            elif name == "get_connections":
                res = client.get_connections(a.get("path"))
            elif name == "exec_node_method":
                res = client.exec_node_method(a.get("path"), a.get("method"), a.get("args"))
            elif name == "snapshot_network":
                res = client.snapshot_network(a.get("path"))
            elif name == "restore_network":
                res = client.restore_network(a.get("snapshot"), a.get("target_parent", "*here"))
            elif name == "get_performance":
                res = client.get_performance(a.get("path"))
            elif name == "validate_network":
                res = client.validate_network(a.get("path"), a.get("depth", 2))
            elif name == "set_flags":
                res = client.set_flags(a.get("path"), a.get("flags", {}))
            elif name == "find_nodes":
                res = client.find_nodes(a.get("path"), a.get("query"), a.get("type"), a.get("depth", 4))
            elif name == "set_node_position":
                res = client.set_node_position(a.get("path"), a.get("x", 0), a.get("y", 0))
            elif name == "timeline":
                res = client.timeline(a.get("action"), a.get("value"))
            elif name == "export_recipe":
                res = client.export_recipe(a.get("path"), a.get("depth", 3))
            elif name == "import_recipe":
                res = client.import_recipe(a.get("recipe"), a.get("target_parent", "*here"))
            elif name == "save_tox":
                res = client.save_tox(a.get("path"), a.get("file_path"))
            else:
                res = {"ok": False, "error": "unknown tool"}
            
            # Format as JSON string for tool output
            import json as _json
            text = _json.dumps(res, indent=2)
        except Exception as e:
            text = f"error: {e}"
        return [types.TextContent(type="text", text=text)]

    # ------------------------------------------------------------------
    # Live resource templates (agent_mcp-style td:// subscriptions)
    # ------------------------------------------------------------------
    @app.list_resource_templates()
    async def list_resource_templates():
        return [
            types.ResourceTemplate("td://node/{path}", "Live TouchDesigner node metadata",
                                    "application/json", "Node name, type, path, child count"),
            types.ResourceTemplate("td://chop/{path}", "Live CHOP channel names",
                                    "application/json", "Channel names of a CHOP"),
            types.ResourceTemplate("td://errors/{path}", "Live cook/compile errors for a node",
                                    "application/json", "Error strings for a node"),
            types.ResourceTemplate("td://project/info", "Live TouchDesigner project info",
                                    "application/json", "TD build, FPS, file name"),
        ]

    @app.read_resource()
    async def read_resource(uri):
        res = client.get_resource(uri)
        return [types.TextResourceContents(
            uri=uri, mimeType="application/json", text=json.dumps(res, indent=2))]

    # ------------------------------------------------------------------
    # Prompt templates — teach the agent which tool combos to use
    # (counters the "models forget to use the server" problem)
    # ------------------------------------------------------------------
    _PROMPTS = {
        "build_and_verify_workflow": (
            "You are building a TouchDesigner network. Follow this loop:\n"
            "1. Use td_docs_search / td_docs_operator to confirm operator names and parameter names.\n"
            "2. create_node for each operator (auto-layout handles positions; pass nodeX/nodeY only to override).\n"
            "3. set_parameters on each new node with the real parameter names.\n"
            "4. connect_nodes to wire outputs to inputs — never leave wires disconnected.\n"
            "5. get_errors on every new/modified node to confirm it cooks without errors.\n"
            "6. auto_layout then set_node_color to color-code (blue=input, orange=effect, green=output).\n"
            "7. capture_viewport / build_and_verify to confirm the output is not black/flat.\n"
            "8. map_network to sanity-check the topology before finishing."
        ),
        "fix_network_errors": (
            "A TouchDesigner node is erroring. Diagnose in order:\n"
            "1. get_errors(<path>) to read the exact error strings.\n"
            "2. get_node(<path>) to inspect connections, non-default params and type.\n"
            "3. If it is a parameter name issue, call td_docs_parameter(<op_name>) for valid names.\n"
            "4. If an input is missing/unconnected, connect_nodes to wire the correct source.\n"
            "5. Re-run get_errors to confirm the fix. Note: TD refreshes its error cache on frame boundaries, "
            "so check errors in a request separate from the mutation."
        ),
        "rag_first": (
            "Before building or editing any TouchDesigner network, consult the documentation RAG:\n"
            "- td_docs_search(query) for operator/python/GLSL/tutorial chunks.\n"
            "- td_docs_operator(name) for full parameter + connector + Python class specs.\n"
            "- td_docs_parameter(op_name) to avoid invalid parameter names.\n"
            "Use the returned context to choose correct operator types and parameter names; this prevents "
            "cook errors and hallucinated APIs."
        ),
    }

    @app.list_prompts()
    async def list_prompts():
        return [
            types.Prompt("build_and_verify_workflow",
                         "Recipe for building a verified, wired, color-coded network.", []),
            types.Prompt("fix_network_errors",
                         "Step-by-step diagnosis and repair of a cooking/compile error.", []),
            types.Prompt("rag_first",
                         "Always consult documentation RAG before building or editing.", []),
        ]

    @app.get_prompt()
    async def get_prompt(name, arguments):
        body = _PROMPTS.get(name)
        if body is None:
            return types.GetPromptResult(
                description="unknown prompt",
                messages=[types.PromptMessage(
                    role="user", content=types.TextContent(type="text", text="unknown prompt"))])
        return types.GetPromptResult(
            description=name,
            messages=[types.PromptMessage(
                role="user", content=types.TextContent(type="text", text=body))])

    return app


def _main_mcp(host=DEFAULT_HOST, port=DEFAULT_PORT, auth_token=None, anchor=False):
    from mcp.server.stdio import stdio_server
    import anyio

    app = create_server(host=host, port=port, auth_token=auth_token, anchor=anchor)

    async def run():
        async with stdio_server() as (r, w):
            await app.run(r, w, app.create_initialization_options())

    anyio.run(run)


def _main():
    ap = argparse.ArgumentParser(description="td-mcp live bridge client")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--anchor", action="store_true",
                    help="attach a doc 'shot' (RAG context) to each tool response")
    ap.add_argument("--auth-token", default=None,
                    help="Bearer auth token (or set TD_MCP_AUTH_TOKEN env)")
    ap.add_argument("--transport", choices=["http", "ws"], default="http",
                    help="transport: http (default) or ws (WebSocket)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    p = sub.add_parser("create")
    p.add_argument("path"); p.add_argument("type"); p.add_argument("--name")
    p = sub.add_parser("delete"); p.add_argument("path")
    p = sub.add_parser("set"); p.add_argument("path"); p.add_argument("json")
    p = sub.add_parser("get"); p.add_argument("path")
    p = sub.add_parser("errors"); p.add_argument("path")
    p = sub.add_parser("exec"); p.add_argument("code")
    p = sub.add_parser("list"); p.add_argument("path")
    sub.add_parser("info")
    p = sub.add_parser("capture"); p.add_argument("path")
    p = sub.add_parser("resource"); p.add_argument("uri")
    sub.add_parser("describe")
    p = sub.add_parser("batch"); p.add_argument("json")
    p = sub.add_parser("connect"); p.add_argument("from_path"); p.add_argument("to_path")
    p.add_argument("--from-output", type=int, default=0); p.add_argument("--to-input", type=int, default=0)
    p = sub.add_parser("rename"); p.add_argument("path"); p.add_argument("new_name")
    p = sub.add_parser("copy"); p.add_argument("path"); p.add_argument("new_parent"); p.add_argument("--new-name")
    p = sub.add_parser("layout"); p.add_argument("path")
    p.add_argument("--direction", default="left-right"); p.add_argument("--spacing-x", type=int, default=200); p.add_argument("--spacing-y", type=int, default=200)
    sub.add_parser("node"); p.add_argument("path")
    p = sub.add_parser("color"); p.add_argument("path")
    p.add_argument("r", type=float); p.add_argument("g", type=float); p.add_argument("b", type=float)
    p = sub.add_parser("comment"); p.add_argument("path"); p.add_argument("--comment", default=None); p.add_argument("--tags", default=None)
    p = sub.add_parser("map"); p.add_argument("path"); p.add_argument("--depth", type=int, default=2)
    p = sub.add_parser("disconnect"); p.add_argument("from_path"); p.add_argument("to_path"); p.add_argument("--to-input", type=int, default=0)
    p = sub.add_parser("connections"); p.add_argument("path")
    p = sub.add_parser("exec"); p.add_argument("path"); p.add_argument("method"); p.add_argument("args", nargs="*", default=[])
    p = sub.add_parser("snapshot"); p.add_argument("path")
    p = sub.add_parser("restore"); p.add_argument("snapshot"); p.add_argument("--parent", default="*here")
    p = sub.add_parser("performance"); p.add_argument("path")
    p = sub.add_parser("validate"); p.add_argument("path"); p.add_argument("--depth", type=int, default=2)
    p = sub.add_parser("flags"); p.add_argument("path"); p.add_argument("--bypass", action="store_true"); p.add_argument("--viewer", action="store_true"); p.add_argument("--exclude", action="store_true")
    p = sub.add_parser("find"); p.add_argument("path"); p.add_argument("--query", default=None); p.add_argument("--type", default=None); p.add_argument("--depth", type=int, default=4)
    p = sub.add_parser("move"); p.add_argument("path"); p.add_argument("x", type=int); p.add_argument("y", type=int)
    p = sub.add_parser("timeline"); p.add_argument("action"); p.add_argument("--value", type=float, default=None)
    p = sub.add_parser("export"); p.add_argument("path"); p.add_argument("--depth", type=int, default=3)
    p = sub.add_parser("import"); p.add_argument("json"); p.add_argument("--parent", default="*here")
    p = sub.add_parser("save"); p.add_argument("path"); p.add_argument("--file", default=None)

    args = ap.parse_args()
    if args.transport == "ws":
        if not WS_AVAILABLE:
            print("Error: websocket-client not installed (pip install websocket-client)")
            sys.exit(1)
        c = WSClient(args.host, args.port, auth_token=args.auth_token)
        c.connect()
    else:
        c = TDClient(args.host, args.port, anchor=args.anchor, auth_token=args.auth_token)

    if args.cmd == "status":
        print(json.dumps(c.status(), indent=2))
    elif args.cmd == "create":
        print(json.dumps(c.create_node(args.path, args.type, args.name), indent=2))
    elif args.cmd == "delete":
        print(json.dumps(c.delete_node(args.path), indent=2))
    elif args.cmd == "set":
        print(json.dumps(c.set_parameters(args.path, json.loads(args.json)), indent=2))
    elif args.cmd == "get":
        print(json.dumps(c.get_parameters(args.path), indent=2))
    elif args.cmd == "errors":
        print(json.dumps(c.get_errors(args.path), indent=2))
    elif args.cmd == "exec":
        print(json.dumps(c.execute_python(args.code), indent=2))
    elif args.cmd == "list":
        print(json.dumps(c.list_nodes(args.path), indent=2))
    elif args.cmd == "info":
        print(json.dumps(c.project_info(), indent=2))
    elif args.cmd == "capture":
        print(json.dumps(c.capture_viewport(args.path), indent=2))
    elif args.cmd == "resource":
        print(json.dumps(c.get_resource(args.uri), indent=2))
    elif args.cmd == "describe":
        print(json.dumps(c.describe_td_tools(), indent=2))
    elif args.cmd == "batch":
        print(json.dumps(c.batch(json.loads(args.json)), indent=2))
    elif args.cmd == "connect":
        print(json.dumps(c.connect_nodes(args.from_path, args.to_path, args.from_output, args.to_input), indent=2))
    elif args.cmd == "rename":
        print(json.dumps(c.rename_node(args.path, args.new_name), indent=2))
    elif args.cmd == "copy":
        print(json.dumps(c.copy_node(args.path, args.new_parent, args.new_name), indent=2))
    elif args.cmd == "layout":
        print(json.dumps(c.auto_layout(args.path, args.direction, args.spacing_x, args.spacing_y), indent=2))
    elif args.cmd == "node":
        print(json.dumps(c.get_node(args.path), indent=2))
    elif args.cmd == "color":
        print(json.dumps(c.set_node_color(args.path, args.r, args.g, args.b), indent=2))
    elif args.cmd == "comment":
        print(json.dumps(c.set_node_comment(args.path, args.comment, args.tags), indent=2))
    elif args.cmd == "map":
        print(json.dumps(c.map_network(args.path, args.depth), indent=2))
    elif args.cmd == "disconnect":
        print(json.dumps(c.disconnect_nodes(args.from_path, args.to_path, args.to_input), indent=2))
    elif args.cmd == "connections":
        print(json.dumps(c.get_connections(args.path), indent=2))
    elif args.cmd == "exec":
        print(json.dumps(c.exec_node_method(args.path, args.method, args.args), indent=2))
    elif args.cmd == "snapshot":
        print(json.dumps(c.snapshot_network(args.path), indent=2))
    elif args.cmd == "restore":
        print(json.dumps(c.restore_network(args.snapshot, args.parent), indent=2))
    elif args.cmd == "performance":
        print(json.dumps(c.get_performance(args.path), indent=2))
    elif args.cmd == "validate":
        print(json.dumps(c.validate_network(args.path, args.depth), indent=2))
    elif args.cmd == "flags":
        flags = {}
        if args.bypass:
            flags["bypass"] = True
        if args.viewer:
            flags["viewer"] = True
        if args.exclude:
            flags["excludeFromCook"] = True
        print(json.dumps(c.set_flags(args.path, flags), indent=2))
    elif args.cmd == "find":
        print(json.dumps(c.find_nodes(args.path, args.query, args.type, args.depth), indent=2))
    elif args.cmd == "move":
        print(json.dumps(c.set_node_position(args.path, args.x, args.y), indent=2))
    elif args.cmd == "timeline":
        print(json.dumps(c.timeline(args.action, args.value), indent=2))
    elif args.cmd == "export":
        print(json.dumps(c.export_recipe(args.path, args.depth), indent=2))
    elif args.cmd == "import":
        print(json.dumps(c.import_recipe(json.loads(args.json), args.parent), indent=2))
    elif args.cmd == "save":
        print(json.dumps(c.save_tox(args.path, args.file), indent=2))


def main():
    if os.environ.get("TD_MCP_MODE") == "mcp" or "--mcp" in sys.argv:
        # Simple parser for MCP option flags
        sys.argv = [a for a in sys.argv if a != "--mcp"]
        parser = argparse.ArgumentParser(description="td-mcp live MCP server")
        parser.add_argument("--host", default=DEFAULT_HOST)
        parser.add_argument("--port", type=int, default=DEFAULT_PORT)
        parser.add_argument("--anchor", action="store_true")
        parser.add_argument("--auth-token", default=None)
        args, _ = parser.parse_known_args()
        _main_mcp(host=args.host, port=args.port, auth_token=args.auth_token, anchor=args.anchor)
    else:
        _main()


if __name__ == "__main__":
    main()
