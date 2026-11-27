"""td-mcp live server: Streamable HTTP MCP server for TouchDesigner.

Implements the MCP Streamable HTTP + stdio spec (stateless, MCP SDK 2.0):
- JSON-RPC over POST (initialize, tools/list, tools/call, prompts/list, resources/list, server/discover)
- Stateless Streamable HTTP via SDK v2 (no Mcp-Session-Id, stateless_http=True)
- DNS-rebind protection (built into SDK for loopback)
- Backward-compatible stdio mode for Claude Desktop / Cursor
- Bearer-token auth via per-request middleware (TD_MCP_AUTH_TOKEN)
- Talks to TD bridge at http://127.0.0.1:9980 (or TD_MCP_HOST:TD_MCP_PORT)

CLI:
  python -m td_mcp.server_live --mcp           # stdio MCP (what agents/clients run)
  python -m td_mcp.server_live --http          # Streamable HTTP on 127.0.0.1:8765
  python -m td_mcp.server_live create ...      # legacy CLI against bridge
  (TD_MCP_MODE=mcp also selects stdio MCP. With NO arguments this is the
  legacy CLI, which exits asking for a subcommand -- an MCP client launched
  that way sees "Connection closed" during initialize.)
"""

import argparse
import hmac
import json
import os
import sys

import urllib.request
import urllib.error

from td_mcp import __version__
from td_mcp import instructions as _instructions
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

    def get_errors(self, path, include_warnings=True, include_shader=True,
                   recurse=False):
        return self._call("get_errors", {"path": path,
                                         "include_warnings": include_warnings,
                                         "include_shader": include_shader,
                                         "recurse": recurse})

    def clear_script_errors(self, path, recurse=False):
        return self._call("clear_script_errors", {"path": path,
                                                  "recurse": recurse})

    def execute_python(self, code):
        return self._call("execute_python", {"code": code})

    def list_nodes(self, path):
        return self._call("list_nodes", {"path": path})

    def project_info(self):
        return self._call("project_info", {})

    def capture_viewport(self, path, detail="normal", width=None):
        args = {"path": path, "detailLevel": detail}
        if width:
            args["width"] = width
        result = self._call("capture_viewport", args)
        return _attach_png(result, "TouchDesigner viewport capture")

    def screenshot(self, args):
        """What is on screen (a viewer window, TD's window, a display), as an
        image the model can look at -- see the bridge's `_screenshot`."""
        a = dict(args or {})
        if "detail" in a and "detailLevel" not in a:
            a["detailLevel"] = a.pop("detail")
        return _attach_png(self._call("screenshot", a), "TouchDesigner screenshot")

    def get_resource(self, uri):
        return self._call("get_resource", {"uri": uri})

    def describe_td_tools(self):
        return self._call("describe_td_tools", {})

    def batch(self, ops):
        return self._call("batch", {"ops": ops})

    def observe(self, path, frames=3, detail="normal"):
        return self._call("observe", {"path": path, "frames": frames,
                                      "detailLevel": detail})

    def read_chop(self, path, channel=None, samples=10, format="stats"):
        return self._call("read_chop", {"path": path, "channel": channel,
                                        "samples": samples, "format": format})

    def diff_chop(self, path, other_path, channel=None):
        return self._call("diff_chop", {"path": path, "other_path": other_path,
                                        "channel": channel})

    def read_pop(self, path, attribute=None, format="stats", max_points=None):
        return self._call("read_pop", {"path": path, "attribute": attribute,
                                       "format": format,
                                       "max_points": max_points})

    def read_top(self, path, detail="brief"):
        return self._call("read_top", {"path": path, "detailLevel": detail})

    def read_dat(self, path, rows=10, format="stats"):
        return self._call("read_dat", {"path": path, "rows": rows,
                                       "format": format})

    def set_dat_text(self, path, text, expected_sha256=None):
        return self._call("set_dat_text", {"path": path, "text": text,
                                           "expected_sha256": expected_sha256})

    def scan_network(self, path, depth=3):
        return self._call("scan_network", {"path": path, "depth": depth})

    def build_and_verify(self, path, op_type, params=None, name=None):
        """Create → set parameters → errors → viewport verdict → offline heal.

        The last hop is the offline↔live integration point (§3.1): after
        capturing the viewport, the client asks the offline orchestrator to
        validate/score the *created network* with both live signals folded in
        — so a flat render or a script traceback drags the score down and
        produces self-heal candidates instead of being silently accepted.

        Both signals used to be dropped on the floor. ``errs`` was captured and
        returned to the caller but never passed to the scorer, and the whole
        ``capture_viewport`` envelope was passed where its inner ``verdict``
        was expected, so ``is_black``/``is_flat`` read as absent and no
        viewport penalty could ever fire.
        """
        created = self.create_node(path, op_type, name)
        if not created.get("ok"):
            return created
        node_path = created.get("path")
        if params:
            self.set_parameters(node_path, params)
        errs = self.get_errors(node_path)
        preview = self.capture_viewport(node_path)
        verdict = preview.get("verdict") if isinstance(preview, dict) else None
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
                viewport_verdict=verdict,
                live_diagnostics=errs)
        except Exception:  # noqa: BLE001
            offline = None
        out = {"created": created, "errors": errs, "preview": preview}
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
        """Token-efficient read (vedant1317): non-default params only.

        Uses the bridge's own ``non_default`` filter. It used to call get_node
        and return *its* params -- every parameter, default or not.
        """
        result = self._call("get_parameters", {"path": path, "non_default": True,
                                               "detailLevel": "full"})
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

    def timeline(self, action, value=None, force=False):
        return self._call("timeline", {"action": action, "value": value,
                                       "force": force})

    def export_recipe(self, path, depth=3):
        return self._call("export_recipe", {"path": path, "depth": depth})

    def import_recipe(self, recipe, target_parent="*here"):
        return self._call("import_recipe", {"recipe": recipe, "target_parent": target_parent})

    def build_network(self, spec, target_parent=None, replace=False):
        """Resolve parameter names offline (live-probed catalog), then build.

        The agent's friendly names ('brightness', 'resolution', 'monochrome')
        are mapped to TouchDesigner's before the bridge creates anything, and
        what was changed rides back under ``resolver`` so the agent learns the
        real names for next time.
        """
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except ValueError as exc:
                return {"ok": False, "error": "bad spec json: %s" % exc}
        resolver = []
        try:
            from td_mcp import param_resolver
            spec, resolver, _ok = param_resolver.resolve_build(spec or {})
        except Exception:  # noqa: BLE001 - resolution is advisory
            pass
        result = self._call("build_network", {"spec": spec, "target_parent": target_parent,
                                               "replace": replace})
        if isinstance(result, dict) and resolver:
            result["resolver"] = resolver
        return result

    def save_tox(self, path, file_path=None):
        return self._call("save_tox", {"path": path, "file_path": file_path})

    def health(self, path=None, seconds=0.5):
        return self._call("health", {"path": path, "seconds": seconds})

    def annotate(self, action="list", path=None, mode=None, title=None, body=None,
                 around=None, rect=None, color=None, name=None):
        return self._call("annotate", {"action": action, "path": path, "mode": mode,
                                       "title": title, "body": body, "around": around,
                                       "rect": rect, "color": color, "name": name})


def _attach_png(result, description):
    """Image-block feedback (mintiasaikoh): when the bridge returns a PNG file,
    attach it as base64 so the MCP client renders it -- the model SEES it --
    instead of only reading the verdict text."""
    if isinstance(result, dict) and result.get("ok") and result.get("file"):
        try:
            import base64
            with open(result["file"], "rb") as f:
                result["image"] = {
                    "mime_type": "image/png",
                    "data": base64.b64encode(f.read()).decode("ascii"),
                    "description": (result.get("verdict") or {}).get("caption", description),
                }
        except Exception:  # noqa: BLE001
            pass
    return result


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


# Register all bridge tools.
#
# Every tool FORWARDS its arguments to the bridge unchanged. The registry used
# to name each argument by hand, and quietly dropped the ones nobody listed:
# create_node lost nodeX/nodeY, list_nodes and set_parameters lost
# detailLevel, get_parameters and get_performance lost every filter -- so a
# feature added to a bridge handler could not be reached through MCP at all.
# Only tools that do real work on this side of the wire are written out.
def _forward(name):
    def call(c, a):
        a = dict(a or {})
        if "detail" in a and "detailLevel" not in a:
            a["detailLevel"] = a.pop("detail")
        if name == "build_and_verify" and "op_type" in a and "type" not in a:
            a["type"] = a.pop("op_type")
        return c._call(name, a)
    return _wrap_tool(call)


_CLIENT_SIDE = {
    # attaches the PNG as an MCP image block
    "capture_viewport": _wrap_tool(lambda c, a: c.capture_viewport(
        a.get("path"), a.get("detail") or a.get("detailLevel", "normal"),
        width=a.get("width"))),
    # `type` for consistency with create_node (and with the bridge's own
    # handler); `op_type` still accepted for clients written against the old
    # schema. A test pins that the schema and this lambda name the same keys.
    "build_and_verify": _wrap_tool(lambda c, a: c.build_and_verify(
        a.get("path"), a.get("type") or a.get("op_type"), a.get("params"), a.get("name"))),
    "get_non_default_params": _wrap_tool(lambda c, a: c.get_non_default_params(a.get("path"))),
    "screenshot": _wrap_tool(lambda c, a: c.screenshot(a)),  # attaches the PNG
    # resolves parameter names against the catalog before the bridge sees them
    "build_network": _wrap_tool(lambda c, a: c.build_network(
        a.get("spec"), a.get("target_parent"), a.get("replace", False))),
    # adds the block parameters a throwaway instance cannot show (see below)
    "describe_op_type": _wrap_tool(lambda c, a: _with_catalog_blocks(
        c._call("describe_op_type", dict(a or {})), a or {})),
}


def _with_catalog_blocks(result, args):
    """Add the sequence block parameters the bridge could not see.

    The bridge describes a type from a fresh instance and will not cook it
    (iterating ``OP.seq`` would, verified on 2025.33230 -- and cooking is what
    opens a device). A fresh Primitive POP therefore shows no ``pt0posx`` at
    all, though ``set_parameters`` accepts it. The catalog recorded those
    after one cook in /sys/quiet, so they are added here, marked
    ``from_catalog``, under the same `pattern` / `page` filter.
    """
    if not isinstance(result, dict) or not result.get("ok"):
        return result
    try:
        from td_mcp.kb import param_catalog
        info = param_catalog.op_info(result.get("op_type") or "")
    except Exception:  # noqa: BLE001 - the description stands without it
        return result
    if not info:
        return result
    seen = {r.get("name") for r in result.get("parameters") or []}
    pattern, page = args.get("pattern"), args.get("page")
    import fnmatch as _fnmatch
    pat = (pattern or "").lower()
    if pat and not any(ch in pat for ch in "*?["):
        pat = "*%s*" % pat
    extra = []
    for p in info.get("pars") or []:
        if p["name"] in seen or not (p.get("sequence") or p.get("after_cook")):
            continue
        if page and (p.get("page") or "").lower() != str(page).lower():
            continue
        if pat and not (_fnmatch.fnmatchcase(p["name"].lower(), pat)
                        or _fnmatch.fnmatchcase((p.get("label") or "").lower(), pat)):
            continue
        row = dict(p)
        if args.get("include_menus") is False:
            row.pop("menu", None)
            row.pop("menu_labels", None)
        row["from_catalog"] = True
        extra.append(row)
    if extra:
        result = dict(result)
        result["parameters"] = list(result.get("parameters") or []) + extra
        result["count"] = len(result["parameters"])
        result["from_catalog"] = ("%d parameter(s) exist only once the operator has cooked "
                                  "(sequence blocks, script callbacks); listed from the "
                                  "catalog of TouchDesigner %s" % (len(extra), param_catalog.build()))
        if info.get("sequences") and not result.get("sequences"):
            result["sequences"] = info["sequences"]
    return result

TOOL_REGISTRY = {}  # filled from _TOOL_META below, so the two cannot drift


# ---------------------------------------------------------------------------
# Tool metadata — single source of truth for BOTH the stdio MCP server and
# the Streamable-HTTP server (so the HTTP `tools/list` is no longer empty and
# both surfaces share identical descriptions / schemas / risk annotations).
# ---------------------------------------------------------------------------
# (name, description, properties) — annotations are derived from risk_class().
_S = {"type": "string"}
_I = {"type": "integer"}
_B = {"type": "boolean"}
_N = {"type": "number"}
_O = {"type": "object"}
_A = {"type": "array"}
_PATH = {"type": "string", "description": "Operator path, e.g. /project1/noise1. *here = the "
         "network the user is viewing, *this = the selected operator."}
_DETAIL = {"type": "string", "enum": ["brief", "normal", "full"],
           "description": "Output size: brief < normal (default) < full."}

_TOOL_META = [
    ("create_node", "Create one operator. `type` is the TD class name (noiseTOP, lfoCHOP, "
     "gridPOP, textDAT, baseCOMP); display names ('Noise TOP') are normalized. Without "
     "`path` it lands in the network the user is viewing (never inside the TD_MCP "
     "component); without nodeX/nodeY it is auto-placed in a family lane. Look up "
     "parameter names first with describe_op_type, then set them with set_parameters.",
     {"path": dict(_PATH, description="Parent COMP to create in."), "type": _S,
      "name": _S, "nodeX": _I, "nodeY": _I}),
    ("delete_node", "Delete one operator (one undo step). Refuses the TD_MCP component, "
     "its ancestors and TD_MCP_PROTECTED_PATHS.",
     {"path": _PATH}),
    ("set_parameters", "Set parameters by their exact name: {'period': 2, 'mono': true}. "
     "Tuplets take a list ({'resolution': [1280, 720]}, {'t': [0, 0, 1]}); menus take "
     "the token or its label; a pulse parameter set to true is pulsed. Every write is read "
     "back: unknown names fail with did-you-mean, an invalid menu token FAILS (TD itself "
     "ignores it silently), and silent clamps or a discarded expression come back under "
     "`notes`. For an expression pass {'name': {'expr': 'absTime.seconds'}}.",
     {"path": _PATH, "params": _O, "detailLevel": _DETAIL}),
    ("get_parameters", "Read parameter values as TouchDesigner shows them (eval(), not the "
     "constant slot -- an expression-mode par's `val` is stale). Non-constant pars are "
     "described under `modes` (expression text, bind master, export source). Filter with "
     "`names` (comma list or globs: 'resolution*,period') or non_default=true.",
     {"path": _PATH, "names": _S, "non_default": _B, "detailLevel": _DETAIL}),
    ("get_parameter_info", "Full schema of an existing operator's parameters: label, value, "
     "mode, expression, default, page, style, bounds, menu tokens. For a type you have not "
     "created yet use describe_op_type.",
     {"path": _PATH, "names": _S}),
    ("describe_op_type", "Look before you guess: parameter names, labels, styles, creation "
     "defaults and menu tokens for an operator TYPE, from a throwaway instance (cached per "
     "session). TD abbreviates unpredictably -- Blur TOP amount is `size`, Noise TOP "
     "monochrome is `mono`, Lag CHOP has lag1/lag2 -- so call this once per unfamiliar type "
     "instead of a failed set_parameters. Filter with `pattern` (name/label glob) or `page`.",
     {"op_type": _S, "pattern": _S, "page": _S, "include_menus": _B}),
    ("pulse_parameter", "Pulse a pulse-type parameter (reset, reload, start, cook). Lists the "
     "operator's pulse parameters when the name is wrong.",
     {"path": _PATH, "param": _S}),
    ("bind_parameter_expression", "Put a parameter in expression mode and report whether the "
     "expression evaluates (eval error + the operator's errors come back).",
     {"path": _PATH, "param": _S, "expression": _S}),
    ("get_errors", "All four TouchDesigner diagnostic surfaces for a node: cook "
     "errors, warnings, script tracebacks (scriptErrors) and GLSL compile "
     "errors (from the docked Info DAT). Tagged by kind and severity; "
     "errorCount/hasErrors mean what the network shows red. Call after every build "
     "step, with recurse=true on a COMP.",
     {"path": _PATH,
      "include_warnings": _B,
      "include_shader": _B,
      "recurse": _B}),
    ("clear_script_errors", "Clear a node's script errors. They persist until "
     "cleared, so a transient failure keeps badging an operator whose code is "
     "already correct -- call this after fixing one, before re-verifying.",
     {"path": _PATH, "recurse": _B}),
    ("execute_python", "Run Python on TouchDesigner's main thread; set `result` to return a "
     "value. Blocked builtins: open/eval/exec/compile/globals/locals/__import__ (use import "
     "statements and pathlib). Not fenced off the TD_MCP component -- prefer the typed "
     "tools, which are. Disabled when the bridge runs with TD_MCP_ALLOW_EXEC=0.",
     {"code": _S}),
    ("list_nodes", "List an operator's children (name, type, opType, path).",
     {"path": _PATH, "detailLevel": _DETAIL}),
    ("project_info", "Project file, TouchDesigner build, fps and bridge version: the first "
     "call of a session.", {}),
    ("capture_viewport", "Capture ANY operator as a PNG plus an is_black/is_flat verdict. A "
     "TOP is captured directly; a CHOP/SOP/POP/DAT/COMP through a transient OP Viewer "
     "(its network-editor viewer: graph, viewport or text), which needs ~0.5-1 s of "
     "frames. Judge a visual result on this, never on a clean error list.",
     {"path": _PATH, "detail": _DETAIL, "width": _I}),
    ("get_resource", "Read a td:// resource (td://node/<path>).", {"uri": _S}),
    ("describe_td_tools", "Bridge manifest: tool list, exec gate, protected paths, version.", {}),
    ("batch", "Run several tool calls in one round-trip: [{'tool': 'set_parameters', "
     "'args': {...}}, ...]. Each call is guarded exactly as a direct call. Not for "
     "capturing a non-TOP: that needs frames, so call capture_viewport directly.",
     {"ops": _A}),
    ("observe", "Capture a still AND judge whether the render is moving. "
     "A network that renders a correct single frame and never changes is "
     "structurally valid, visually plausible and broken; no single-frame "
     "verdict can see it. Returns is_static (None when undecidable).",
     {"path": _PATH, "frames": _I,
      "detail": _DETAIL}),
    ("read_chop", "Read a CHOP. format='stats' (default) reduces each channel to "
     "count/min/max/mean/std -- what the chain did to the data, in four numbers. "
     "format='values' returns raw samples.",
     {"path": _PATH, "channel": _S,
      "samples": _I, "format": _S}),
    ("diff_chop", "Compare two CHOPs channel by channel and report min/max/mean/std "
     "deltas -- the read to make after inserting a filter.",
     {"path": _PATH, "other_path": _S,
      "channel": _S}),
    ("read_pop", "Read a POP. Attribute metadata is always free; values are read "
     "only under a point-count ceiling, because POP.points() accepts start/count "
     "and ignores them -- there is no cheap partial read.",
     {"path": _PATH, "attribute": _S,
      "format": _S, "max_points": _I}),
    ("read_top", "Read TOP metadata: resolution, pixel format, GPU memory.",
     {"path": _PATH, "detail": _DETAIL}),
    ("read_dat", "Read a DAT table. format='stats' (default) returns per-column "
     "stats plus the first and last rows, ~17x smaller than a dump; "
     "format='rows' returns the rows verbatim.",
     {"path": _PATH, "rows": _I,
      "format": _S}),
    ("set_dat_text", "Replace a DAT's text (Python, GLSL, a table as tab/newline text). Pass "
     "the sha256 from the previous write as expected_sha256 to refuse clobbering a "
     "concurrent edit. Python that calls run() from a thread comes back with a "
     "THREADING WARNING.",
     {"path": _PATH, "text": _S, "expected_sha256": _S}),
    ("scan_network", "Topology of a COMP down to `depth`: operators, types, wiring.",
     {"path": _PATH, "depth": _I}),
    ("build_and_verify", "Create -> set -> all four error surfaces -> viewport verdict "
     "-> offline validate/score/heal, in one call. `type` as in create_node "
     "(`op_type` still accepted).",
     {"path": _PATH, "type": _S, "op_type": _S,
      "name": _S, "params": _O}),
    ("connect_nodes", "Wire from_path's output `from_output` into to_path's input "
     "`to_input` (both 0-based). Replaces whatever fed that input.",
     {"from_path": _S, "to_path": _S, "from_output": _I, "to_input": _I}),
    ("rename_node", "Rename an operator. Paths in other operators' parameters and "
     "expressions do NOT follow a rename -- search for them afterwards.",
     {"path": _PATH, "new_name": _S}),
    ("copy_node", "Copy an operator (and a COMP's contents) into new_parent.",
     {"path": _PATH, "new_parent": _S, "new_name": _S}),
    ("auto_layout", "Re-arrange a COMP's children on a grid, left to right by signal flow.",
     {"path": _PATH, "direction": _S, "spacing_x": _I, "spacing_y": _I}),
    ("get_node", "One operator in detail: type, position, flags, connections, "
     "non-default parameters, errors.",
     {"path": _PATH}),
    ("get_non_default_params", "Token-efficient read: only the parameters that differ "
     "from the operator's defaults, with their modes.", {"path": _PATH}),
    ("set_node_color", "Tile colour of an operator (RGB 0..1) -- group related operators.",
     {"path": _PATH, "r": _N, "g": _N, "b": _N}),
    ("set_node_comment", "Attach a comment and tags to an operator.",
     {"path": _PATH, "comment": _S, "tags": _S}),
    ("map_network", "Graphviz DOT of a COMP's wiring and positions, for spatial reasoning.",
     {"path": _PATH, "depth": _I}),
    ("disconnect_nodes", "Break the wire from from_path into to_path's input `to_input`.",
     {"from_path": _S, "to_path": _S, "to_input": _I}),
    ("get_connections", "Inputs and outputs of an operator, or the signal flow of a COMP.",
     {"path": _PATH}),
    ("exec_node_method", "Call a method on an operator (e.g. cook, par reset). Refuses the "
     "TD_MCP component and its ancestors.",
     {"path": _PATH, "method": _S, "args": _A}),
    ("snapshot_network", "Save a COMP to a temporary .tox checkpoint that survives the undo "
     "stack; returns the snapshot path for restore_network.",
     {"path": _PATH}),
    ("restore_network", "Load a snapshot_network checkpoint into target_parent.",
     {"snapshot": _S, "target_parent": _S}),
    ("get_performance", "What is slowing a network down: cook time per operator in ms "
     "(CPU+GPU), budget_pct of one frame, hotspots. An operator that has not cooked "
     "within max_age_frames is marked stale and ranked last (its cook time is history). "
     "A non-COMP path profiles that one operator. samples=N (e.g. 10) cooks the "
     "network once per real frame for N frames and reports the MEDIAN per operator "
     "(peak_ms = worst frame): one reading right after a build includes one-off "
     "costs and can be 50x the steady state.",
     {"path": _PATH, "max_age_frames": _I, "depth": _I, "limit": _I, "samples": _I}),
    ("validate_network", "Scene contract check: unplaced/overlapping nodes and cook errors.",
     {"path": _PATH, "depth": _I}),
    ("health", "What TouchDesigner does NOT call an error, in one read -- run it before saying "
     "a network works. Samples twice with real frames between (`seconds`, default 0.5) and "
     "reports findings by severity: parameter expressions that raise (errors() stays empty "
     "until the operator next cooks), dead binds, extensions that failed to initialise, COMPs "
     "with cooking off, bypassed operators, operators that have never cooked because nothing "
     "pulls them, operators costing over half a frame, a paused or stalled timeline, plus "
     "every error and warning under `path` (default /project1). `clean` is true only with no "
     "error and no warning.",
     {"path": _PATH, "seconds": _N}),
    ("annotate", "Network annotations: comments, titled boxes and network boxes. They are "
     "utility operators, invisible to list_nodes / find_nodes / get_node, so this is the only "
     "tool that reads or edits them. action=list (`path` = the network) | create (`path` = the "
     "network; `around` = operator names to frame, or `rect` [x,y,w,h]; `title`, `body`, "
     "`mode` annotate|comment|networkbox, `color` [r,g,b], `name`) | update | delete (`path` = "
     "the annotation). Use it to label the sections of a network you build.",
     {"action": {"type": "string", "enum": ["list", "create", "update", "delete"]},
      "path": _PATH, "mode": {"type": "string", "enum": ["annotate", "comment", "networkbox"]},
      "title": _S, "body": _S,
      "around": {"type": "array", "items": {"type": "string"}},
      "rect": {"type": "array", "items": {"type": "number"}},
      "color": {"type": "array", "items": {"type": "number"}}, "name": _S}),
    ("set_flags", "Set operator flags: display, render, bypass, lock, viewer, "
     "allowCooking (COMPs only), excludeFromCook, forceCooking, cloneImmune, pickable, "
     "expose, activeViewer, current, selected. E.g. {'display': true}.",
     {"path": _PATH, "flags": _O}),
    ("find_nodes", "Search descendants. `query` is a substring, or a glob when it contains * ? [ (e.g. 'noise*'); "
     "`type` matches the short type ('noise') or the OPType ('noiseTOP').",
     {"path": _PATH, "query": _S, "type": _S, "depth": _I}),
    ("set_node_position", "Move an operator to (x, y) in network units (Y grows upward).",
     {"path": _PATH, "x": _I, "y": _I}),
    ("timeline", "Global timeline: status | play | pause | frame | seek | rate. "
     "PAUSING IS REFUSED by default -- the bridge's main-thread pump runs on "
     "Frame Start, so a pause stops this server answering at all, including "
     "the call that would resume it. Pass force=true only if a human is at the "
     "TouchDesigner keyboard.",
     {"action": _S, "value": _N,
      "force": _B}),
    ("export_recipe", "Serialize a COMP's operators, parameters and wiring to a JSON "
     "recipe (import_recipe rebuilds it).",
     {"path": _PATH, "depth": _I}),
    ("screenshot", "SEE what is on screen, as an image you can look at. path=<op> "
     "(default target 'viewer'): opens that operator's floating viewer window, "
     "captures it, closes it (keep_open=true leaves it up for the user). "
     "target='td': TouchDesigner's main window -- the network editor, parameters, "
     "what the user is looking at, to guide them. target='desktop' or 'monitor:N': a "
     "whole display. Every target captures a SCREEN REGION: menus and windows over it "
     "are in the picture. 'td'/'desktop'/'monitor' show OTHER APPLICATIONS whenever TD is "
     "not in front, so they require allow_other_apps=true -- set it only after the user "
     "has asked you to look at their screen and brought TouchDesigner forward. A viewer may apply a display mode; judge an operator's colours "
     "from capture_viewport (its pixels), and what the user sees from this.",
     {"path": _PATH, "target": _S, "keep_open": _B, "allow_other_apps": _B,
      "detail": _DETAIL}),
    ("move_window", "Move TouchDesigner's main window (window='td') or an "
     "operator's viewer window (window=<operator path>) to a display: monitor="
     "'165hz' (by refresh rate), 'primary', 'secondary', 'DISPLAY1' or 'index:N'. "
     "Defaults to TD_MCP_MONITOR, the screen the user chose for TouchDesigner; "
     "ui_state lists the displays.",
     {"window": _S, "monitor": _S}),
    ("open_viewer", "Open (or bring to the front) an operator's floating viewer "
     "window -- what right-click > View... opens -- so the user sees the result. "
     "close=true closes it.",
     {"path": _PATH, "close": _B, "borders": _B}),
    ("show_in_editor", "Point the user at an operator: move the network editor to "
     "its network, make it current and selected, frame it, and show `message` in "
     "TouchDesigner's status bar. Changes only what the user is looking at.",
     {"path": _PATH, "message": _S, "frame": _B}),
    ("ui_state", "What the user is looking at: the network shown in each editor "
     "pane, its current and selected operators, the operator under the mouse, "
     "the main window and monitors, perform mode, status bar, timeline. Read this "
     "before guiding someone ('the node you have selected is...').", {}),
    ("build_network", "Build a whole network in ONE call (one Ctrl+Z): create, set parameters, "
     "wire, set flags and DAT text, auto-place left to right, then report every "
     "operator's result and cook errors. `spec` is the format td_validate_build "
     "checks offline: {'operators': [{'name', 'type', 'parameters', 'inputs': "
     "[source|null], 'flags', 'position', 'text'}], 'connections': [...]}. Parameter "
     "names are resolved against the live-probed catalog first. Validate offline, "
     "then build. replace=true overwrites same-named operators in target_parent.",
     {"spec": _O, "target_parent": dict(_PATH, description="COMP to build in."),
      "replace": _B}),
    ("import_recipe", "Rebuild an export_recipe blueprint inside target_parent; reports "
     "any wire it could not reconnect under `unwired`.",
     {"recipe": _O, "target_parent": _S}),
    ("save_tox", "Save a COMP as a .tox file.", {"path": _PATH, "file_path": _S}),
]

#: Arguments a call cannot do without. Advertised as JSON-Schema `required`,
#: so a client validates before the round trip instead of after it.
_REQUIRED = {
    "create_node": ["type"], "delete_node": ["path"], "set_parameters": ["path", "params"],
    "get_parameters": ["path"], "get_parameter_info": ["path"],
    "describe_op_type": ["op_type"], "pulse_parameter": ["path", "param"],
    "bind_parameter_expression": ["path", "param", "expression"],
    "get_errors": ["path"], "clear_script_errors": ["path"], "execute_python": ["code"],
    "capture_viewport": ["path"], "get_resource": ["uri"], "batch": ["ops"],
    "observe": ["path"], "read_chop": ["path"], "diff_chop": ["path", "other_path"],
    "read_pop": ["path"], "read_top": ["path"], "read_dat": ["path"],
    "set_dat_text": ["path", "text"], "build_and_verify": ["path"],
    "connect_nodes": ["from_path", "to_path"], "rename_node": ["path", "new_name"],
    "copy_node": ["path", "new_parent"], "get_node": ["path"],
    "get_non_default_params": ["path"], "set_node_color": ["path", "r", "g", "b"],
    "set_node_comment": ["path"], "disconnect_nodes": ["from_path", "to_path"],
    "get_connections": ["path"], "exec_node_method": ["path", "method"],
    "snapshot_network": ["path"], "restore_network": ["snapshot"],
    "set_flags": ["path", "flags"], "set_node_position": ["path", "x", "y"],
    "timeline": ["action"], "export_recipe": ["path"], "import_recipe": ["recipe"],
    "build_network": ["spec"], "open_viewer": ["path"], "show_in_editor": ["path"],
    "save_tox": ["path"], "annotate": ["action"],
}


def _input_schema(name, props):
    schema = {"type": "object", "properties": props}
    req = [k for k in _REQUIRED.get(name, []) if k in props]
    if req:
        schema["required"] = req
    return schema


TOOL_REGISTRY.update({name: _CLIENT_SIDE.get(name) or _forward(name)
                      for name, *_ in _TOOL_META})


def _http_tool_meta():
    from td_mcp.tools.risk import tool_annotations
    out = []
    for name, desc, props in _TOOL_META:
        out.append({
            "name": name,
            "description": desc,
            "inputSchema": _input_schema(name, props),
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
                       inputSchema=_input_schema(name, props),
                       annotations=tool_annotations(name))
            for name, desc, props in _TOOL_META
        ],
        ttl_ms=60000,
        cache_scope="private",
    )


def _find_image(result):
    """``(holder_key, image_dict)`` for a result carrying a base64 image.

    ``capture_viewport`` puts it at the top level; ``build_and_verify`` nests
    it under ``preview``. ``holder_key`` is None for the top level.
    """
    if not isinstance(result, dict):
        return None
    image = result.get("image")
    if isinstance(image, dict) and image.get("data"):
        return None, image
    preview = result.get("preview")
    if isinstance(preview, dict):
        image = preview.get("image")
        if isinstance(image, dict) and image.get("data"):
            return "preview", image
    return None


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
        content, text_result = [], result
        image = _find_image(result)
        if image is not None:
            # Emit the PNG as an MCP image block so a client can *render* it,
            # and keep the base64 out of the text block: kilobytes of it are
            # useless to a model as prose, and the first MCP client to look
            # for the image found only text.
            holder, payload = image
            text_result = json.loads(json.dumps(_json_safe(result), default=_json_safe))
            slot = text_result if holder is None else text_result.get(holder, {})
            slot["image"] = {k: v for k, v in payload.items() if k != "data"}
            slot["image"]["bytes"] = len(payload["data"]) * 3 // 4
            content.append(types.ImageContent(type="image", data=payload["data"],
                                              mimeType=payload.get("mime_type", "image/png")))
        content.insert(0, types.TextContent(
            type="text", text=json.dumps(_json_safe(text_result), indent=2, default=_json_safe)))
        return types.CallToolResult(content=content, result_type="complete")
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
        instructions=_instructions.LIVE,
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
    # UTF-8 is pinned here, in main(), and never at import: rebinding the
    # process's standard streams is a global side effect, and this package is
    # exec'd inside TouchDesigner by the in-TD tests, where an import-time pin
    # would mutate TD's own Textport streams.
    from td_mcp.util.stdio import pin_stdio_utf8
    pin_stdio_utf8()
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