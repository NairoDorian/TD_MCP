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
  * Exec gate    - TD_MCP_ALLOW_EXEC=0 / TD_MCP_EXEC_MODE=off disables *every*
    route that hands TouchDesigner caller-supplied code, not just
    execute_python: parameter expressions, bind_parameter_expression, DAT
    text, a DAT's file parameter, Script/Execute/GLSL/Expression operator
    creation, exec_node_method, and import_recipe blueprints carrying any of
    those (tdmcp #143).
  * Threading    - execute_python / set_dat_text statically lint submitted
    Python and return a THREADING WARNING when a thread target reaches TD's
    global run(), which corrupts TD silently instead of raising
    (Embody v6.0.251, Derivative-confirmed 2026-08-17). Advisory: the write
    still lands.
  * Versioning   - BRIDGE_VERSION is stamped here and returned by
    project_info/describe so the live server can spot a stale pasted-in
    bridge instead of reporting its tools as unknown (quentinR2 #8).
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

import ast as _ast
import base64
import hashlib
import hmac
import json
import math
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

# Bridge/server version handshake (quentinR2 #8): the bridge is pasted into a
# .toe by hand, so it drifts from the installed td-mcp package silently and
# only shows up as "unknown tool" on a tool added since the paste. Stamping the
# version lets the live server say so on the first call instead. Keep in sync
# with pyproject.toml [project].version -- scripts/check_bridge_stale.py fails
# CI when they diverge.
BRIDGE_VERSION = "1.11.0"

TD_MCP_PORT = int(os.environ.get("TD_MCP_PORT", "9980"))

ALLOW_EXEC = os.environ.get("TD_MCP_ALLOW_EXEC", "1") != "0"
# Two-tier exec mode (simplified): "off" disables execute_python entirely;
# "on" (default) allows it but always refuses obvious dangerous builtins.
# Legacy aliases restricted/standard/full map to "on"; old "off" still works.
EXEC_MODE = os.environ.get("TD_MCP_EXEC_MODE", "on")
if EXEC_MODE in ("restricted", "standard", "full"):
    EXEC_MODE = "on"
if EXEC_MODE not in ("off", "on"):
    EXEC_MODE = "on"
if EXEC_MODE == "off":
    ALLOW_EXEC = False
# Always-refused builtins when exec is on (defensive, not a separate mode).
_EXEC_FORBIDDEN = ("__import__", "open", "exec", "eval", "compile", "globals", "locals")
PROTECTED = [p.strip() for p in os.environ.get("TD_MCP_PROTECTED_PATHS", "").split(",") if p.strip()]
AUTH_TOKEN = os.environ.get("TD_MCP_AUTH_TOKEN") or secrets.token_urlsafe(24)

# Bounded operations (dcc-mcp pattern, §6.1): cap the size/scope of reads and
# writes so an agent cannot balloon the context or freeze the bridge. All caps
# are env-tunable and enforced server-side in the relevant handlers.
MAX_BATCH_SIZE = int(os.environ.get("TD_MCP_MAX_BATCH", "64"))
MAX_PARAMS = int(os.environ.get("TD_MCP_MAX_PARAMS", "256"))
MAX_DEPTH = int(os.environ.get("TD_MCP_MAX_DEPTH", "4"))
MAX_SAMPLES = int(os.environ.get("TD_MCP_MAX_SAMPLES", "4096"))
MAX_ROWS = int(os.environ.get("TD_MCP_MAX_ROWS", "10000"))
MAX_DAT_BYTES = int(os.environ.get("TD_MCP_MAX_DAT_BYTES", "1048576"))  # 1 MiB


def _cap(value, limit):
    """Clamp a numeric value to [0, limit]; returns int."""
    try:
        return max(0, min(int(value), limit))
    except (TypeError, ValueError):
        return limit


# ===========================================================================
# BEGIN PURE HELPERS (no TouchDesigner dependency; exercised by
# tests/bridge_pure.py, which slices this block out of the source and execs
# it, so the bridge is tested as shipped rather than re-implemented.)
# ===========================================================================

# --- Code-bearing route guard (tdmcp #143, 2026-08-11) ---------------------
#
# Disabling `execute_python` is not the same as disabling code execution.
# TouchDesigner runs caller-supplied Python from many other places: a
# parameter expression, an Execute/Script DAT's text, a GLSL TOP's shader,
# a DAT pointed at a file on disk. Before this guard, TD_MCP_EXEC_MODE=off
# blocked exactly one route and left all of those open, so "exec is off"
# was not a claim the bridge could actually honour.
#
# The detector matches on the *trailing* identifier so scriptCHOP,
# glslmultiTOP and textDAT all resolve without an operator-type table.

_EXECUTABLE_OP_FRAGMENTS = (
    "script", "execute", "expression", "evaluate", "glsl", "shaderpark",
)
_CODE_PARAM_PREFIXES = ("callback", "python", "script", "glsl", "shader")
_CODE_PARAM_NAMES = frozenset(("code", "expr", "expression", "bindexpr"))
# Filter/format params that TD evaluates as Python on specific op types.
_TYPE_SPECIFIC_CODE_PARAMS = {
    "groupsop": frozenset(("filter",)),
    "deletesop": frozenset(("filter",)),
    "grouppop": frozenset(("filter",)),
    "deletepop": frozenset(("filter",)),
    "textcomp": frozenset(("customformatting",)),
}
# Pointing a DAT at a file loads that file's contents as the DAT's text --
# for an Execute/Script DAT that is code execution by another name.
_DAT_FILE_PARAMS = frozenset(("file", "syncfile", "loadonstart", "loadonstartpulse"))


def _normalized_ident(value):
    """Lowercase, strip to [a-z0-9]: 'GLSL Multi TOP' -> 'glslmultitop'."""
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _is_code_param(normalized_type, normalized_name):
    if normalized_name in _CODE_PARAM_NAMES:
        return True
    if normalized_name.endswith("expr"):
        return True
    if re.match(r"^ext\d+object$", normalized_name):
        return True
    if any(normalized_name.startswith(p) for p in _CODE_PARAM_PREFIXES):
        return True
    return normalized_name in _TYPE_SPECIFIC_CODE_PARAMS.get(normalized_type, ())


def code_bearing_sources(op_type=None, params=None, text=None):
    """Every way this call could hand TouchDesigner caller-supplied code.

    Returns a list of human-readable source labels (empty = no code route).
    Pure: types/dicts/strings in, list of strings out.
    """
    sources = []
    normalized_type = _normalized_ident(op_type)
    if normalized_type and any(f in normalized_type for f in _EXECUTABLE_OP_FRAGMENTS):
        sources.append("operator type %s" % (op_type,))
    for name in (params or {}):
        normalized_name = _normalized_ident(name)
        value = (params or {}).get(name)
        # A {"expr": ...} form is an expression regardless of the param name.
        is_expr_form = isinstance(value, dict) and "expr" in value
        is_dat_file = (normalized_type.endswith("dat")
                       and normalized_name in _DAT_FILE_PARAMS)
        if is_expr_form or is_dat_file or _is_code_param(normalized_type, normalized_name):
            sources.append("parameter %s" % (name,))
    if text is not None:
        # Any DAT's text can be executed (Execute/Script DATs run it directly,
        # and a Text DAT is one op('...').run() away), so a text write is a
        # code route unless the type is known and is not a DAT.
        if not normalized_type or normalized_type.endswith("dat"):
            sources.append("DAT text")
    seen, ordered = set(), []
    for s in sources:
        if s not in seen:
            seen.add(s)
            ordered.append(s)
    return ordered


def code_route_denied(action, sources):
    """Uniform refusal for a code-bearing call while exec is off."""
    return {
        "ok": False,
        "error": (
            "%s is refused because it carries caller-supplied code (%s) and "
            "execution is disabled (TD_MCP_EXEC_MODE=off / TD_MCP_ALLOW_EXEC=0). "
            "Disabling execution blocks every code route, not just "
            "execute_python. Re-enable exec only for code you trust."
            % (action, ", ".join(sources))
        ),
        "code_sources": list(sources),
        "exec_mode": EXEC_MODE,
    }


# --- Worker-thread run() lint (Embody v6.0.251, 2026-08-17) ----------------
#
# Derivative confirmed on 2026-08-17 that calling TouchDesigner's global
# run() from a worker thread does NOT raise on current builds: it silently
# corrupts TD state and crashes later, far from the call site. Nothing
# catches it at runtime, so the bridge catches it at WRITE time and rides a
# warning back on the response. Advisory only -- the write still lands.
#
# Bounded up front: oversized sources and sources with no "run(" at all skip
# the parse entirely, and each distinct thread target is scanned once.

_THREAD_CTORS = ("Thread", "TDTask")
_THREAD_DISPATCHERS = ("EnqueueTask", "submit")
_THREAD_TARGET_KWARGS = ("target", "task")
_THREAD_BASES = ("Thread", "TDThread", "TDTask", "Timer")
_WORKER_RUN_MAX_BYTES = 131072
_WORKER_RUN_MAX_FINDINGS = 64


def _called_name(node):
    """Trailing callable name of a Call: 'Thread' for threading.Thread(...) too."""
    func = node.func
    if isinstance(func, _ast.Name):
        return func.id
    if isinstance(func, _ast.Attribute):
        return func.attr
    return None


def _module_bound_names(tree):
    """Names bound at MODULE scope only -- a method or local named `run` must
    not silence the lint on exactly the threading-heavy code it exists for."""
    bound = set()
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            bound.add(node.name)
            continue  # do not descend into the body
        if isinstance(node, (_ast.Import, _ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
            continue
        if isinstance(node, _ast.Name) and isinstance(node.ctx, _ast.Store):
            bound.add(node.id)
        stack.extend(_ast.iter_child_nodes(node))
    return bound


def _run_calls_in(node, run_shadowed):
    """(label, lineno) for every worker-hostile run() call inside `node`.

    Only TD's global run() is the hazard: bare run(...) and td.run(...).
    subprocess.run and every other x.run() are deliberately not matched.
    """
    out = []
    for child in _ast.walk(node):
        if not isinstance(child, _ast.Call):
            continue
        func = child.func
        if isinstance(func, _ast.Name) and func.id == "run" and not run_shadowed:
            out.append(("run()", child.lineno))
        elif (isinstance(func, _ast.Attribute) and func.attr == "run"
                and isinstance(func.value, _ast.Name) and func.value.id == "td"):
            out.append(("td.run()", child.lineno))
    return out


def _direct_callees(node, defs):
    """Names called inside `node` that are defined in the same source -- a
    helper the thread target calls runs on the worker thread too."""
    names = []
    for child in _ast.walk(node):
        if isinstance(child, _ast.Call):
            name = _called_name(child)
            if name in defs and name not in names:
                names.append(name)
    return names


def worker_run_findings(source):
    """Findings for code a *thread* will execute that calls TD's global run().

    Covers functions handed to a thread as a target (plus one level of
    helpers they call in the same source) and the run() override of a
    threading.Thread subclass.

    Pure: source string in, list of {function, call, line, via} out.
    Unparseable source lints to nothing -- submitted code may be a fragment,
    and a lint must never be the thing that fails a write.
    """
    if not isinstance(source, str) or not source:
        return []
    if len(source) > _WORKER_RUN_MAX_BYTES or "run(" not in source:
        return []
    try:
        tree = _ast.parse(source)
    except Exception:  # noqa: BLE001
        return []

    defs = {}
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            defs.setdefault(node.name, node)

    run_shadowed = "run" in _module_bound_names(tree)

    resolved = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Call):
            name = _called_name(node)
            if name not in _THREAD_CTORS and name not in _THREAD_DISPATCHERS:
                continue
            expr = None
            for kw in node.keywords:
                if kw.arg in _THREAD_TARGET_KWARGS:
                    expr = kw.value
                    break
            # EnqueueTask(fn, ...) / executor.submit(fn, ...) pass the callable
            # positionally; Thread's first positional is `group`, so positional
            # targets are read for dispatchers only.
            if expr is None and name in _THREAD_DISPATCHERS and node.args:
                expr = node.args[0]
            if expr is None:
                continue
            if isinstance(expr, _ast.Lambda):
                resolved.append(("<lambda>", expr))
            elif isinstance(expr, (_ast.Name, _ast.Attribute)):
                key = expr.id if isinstance(expr, _ast.Name) else expr.attr
                body = defs.get(key)
                if body is not None:
                    resolved.append((key, body))
        elif isinstance(node, _ast.ClassDef):
            bases = set()
            for b in node.bases:
                if isinstance(b, _ast.Name):
                    bases.add(b.id)
                elif isinstance(b, _ast.Attribute):
                    bases.add(b.attr)
            if not bases.intersection(_THREAD_BASES):
                continue
            for stmt in node.body:
                if (isinstance(stmt, (_ast.FunctionDef, _ast.AsyncFunctionDef))
                        and stmt.name == "run"):
                    resolved.append((node.name + ".run", stmt))

    # Scan each distinct target once -- N spawn sites of one worker is one scan.
    scan, scanned = [], set()
    for label, body in resolved:
        key = (label, id(body))
        if key in scanned:
            continue
        scanned.add(key)
        scan.append((label, body, None))
        for callee in _direct_callees(body, defs):
            if defs[callee] is not body:
                scan.append((label, defs[callee], callee))

    findings = []
    for label, body, via in scan:
        for call, line in _run_calls_in(body, run_shadowed):
            findings.append({"function": label, "call": call,
                             "line": line, "via": via})
            if len(findings) >= _WORKER_RUN_MAX_FINDINGS:
                return findings
    return findings


def threading_warning(source, where):
    """THREADING WARNING string for `source`, or None when it is clean."""
    findings = worker_run_findings(source)
    if not findings:
        return None
    detail = "; ".join(
        '"%s" reaches %s at line %d%s'
        % (f["function"], f["call"], f["line"],
           (" (via %s)" % f["via"]) if f["via"] else "")
        for f in findings[:5])
    return (
        "THREADING WARNING: %s -- thread target %s. TouchDesigner's global "
        "run() called off the main thread does not raise; it corrupts TD "
        "state and crashes later, far from the call site "
        "(Derivative-confirmed 2026-08-17). Queue the work to the main "
        "thread instead (e.g. append to a list a CHOP Execute drains)."
        % (where, detail)
    )


def touchdesigner_release(version, build):
    """Public TD release id from app.version/app.build (dcc-mcp #5).

    ``app.version`` still reports the legacy generation ("099"); the public
    year.build identifier lives on ``app.build``. Version-compat checks that
    compare against "2023.11000"-style corpus builds need the latter.
    """
    version = str(version).strip() if version is not None else ""
    build = str(build).strip() if build is not None else ""
    if build:
        if "." in build or not version:
            return build
        return "%s.%s" % (version, build)
    return version or "unknown"


def split_diagnostics(errors, warnings):
    """Errors + warnings as one severity-tagged list plus per-severity counts.

    tdmcp #146: the bridge used to return only ``errors``, so a caller
    counting them read warnings as errors (or never saw them at all).
    """
    items = []
    for e in errors or []:
        items.append({"message": str(e), "severity": "error"})
    for w in warnings or []:
        items.append({"message": str(w), "severity": "warning"})
    return {
        "diagnostics": items,
        "total": len(items),
        "error_count": sum(1 for i in items if i["severity"] == "error"),
        "warning_count": sum(1 for i in items if i["severity"] == "warning"),
    }

# ===========================================================================
# END PURE HELPERS
# ===========================================================================

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
    if not ALLOW_EXEC:
        sources = code_bearing_sources(op_type=type)
        if sources:
            return code_route_denied("create_node", sources)
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


def _json_safe(value):
    """Recursively convert a TD Python value to JSON-safe form (TDPilot exec shape).

    Handles non-finite floats (inf / -inf / nan -> None, since those produce
    invalid JSON tokens) and numpy scalars/arrays so TouchDesigner's numpyArray()
    output serializes to valid JSON (mrinalghosh: emit valid values for
    non-finite floats and serialize numpy scalars as numbers, not str()).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        # Non-finite floats produce invalid JSON (Infinity / NaN tokens).
        if not math.isfinite(value):
            return None
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return value
    # numpy scalars (np.float64, np.int32, np.bool_, ...) → native Python.
    try:
        import numpy as _np
        if isinstance(value, _np.generic):
            return _json_safe(value.item())
        if isinstance(value, _np.ndarray):
            return [_json_safe(v) for v in value.tolist()]
    except ImportError:
        pass
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in list(value)]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    # TD objects: prefer .path/.name, then x/y/z vector-ish attributes.
    if hasattr(value, "path") and hasattr(value, "name"):
        return {"path": str(value.path), "name": str(value.name),
                "type": str(getattr(value, "type", type(value).__name__))}
    if all(hasattr(value, a) for a in ("x", "y")):
        return [_json_safe(value.x), _json_safe(value.y),
                _json_safe(getattr(value, "z", None)),
                _json_safe(getattr(value, "w", None))]
    return str(value)


def _serialize_exec_result(value):
    structured_types = (type(None), bool, int, float, str, dict, list, tuple, set)
    return {
        "result": _json_safe(value),
        "result_type": type(value).__name__,
        "result_is_structured": isinstance(value, structured_types),
    }


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


def _do_get_parameter_info(path, names=None):
    """Full per-parameter schema (touch_mcp `td_get_parameter_info` idea).

    ``names`` is a comma-separated string or list of parameter names; empty
    means "all". Returns per-parameter label/value/mode/default/page/style and
    bounds/menu info where available.
    """
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    if isinstance(names, str):
        names = [n.strip() for n in names.split(",") if n.strip()]
    if not names:
        names = [p.name for p in node.par]
    out = {}
    for nm in names:
        par = getattr(node.par, nm, None)
        if par is None:
            continue
        entry = {
            "name": par.name,
            "label": getattr(par, "label", nm),
            "value": _json_safe(par.eval() if hasattr(par, "eval") else par.val),
            "mode": str(getattr(par, "mode", "")),
            "default": _json_safe(getattr(par, "default", None)),
            "page": str(getattr(getattr(par, "page", None), "name", None) or ""),
            "style": str(getattr(par, "style", "")),
        }
        for attr in ("min", "max", "clampMin", "clampMax"):
            try:
                entry[attr] = _json_safe(getattr(par, attr))
            except Exception:  # noqa: BLE001
                pass
        try:
            if str(getattr(par, "style", "")) in ("Menu", "MenuIndex", "Str"):
                entry["menuNames"] = [str(x) for x in getattr(par, "menuNames", [])]
                entry["menuLabels"] = [str(x) for x in getattr(par, "menuLabels", [])]
        except Exception:  # noqa: BLE001
            pass
        out[nm] = entry
    return {"ok": True, "parameters": out}


def _do_bind_parameter_expression(path, param, expression):
    """Bind a parameter expression with eval-error reporting (mrinalghosh idea).

    Sets ``par.expr``, snaps ``par.mode`` to EXPRESSION, evals, and returns any
    *new* cook errors so a bad expression never silently no-ops.
    """
    if not ALLOW_EXEC:
        return code_route_denied("bind_parameter_expression",
                                 ["parameter expression %s" % (param,)])
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    par = getattr(node.par, param, None)
    if par is None:
        return {"ok": False, "error": f"unknown parameter {param!r}"}
    try:
        before = [str(e) for e in getattr(node, "errors", []) or []]
    except Exception:  # noqa: BLE001
        before = []
    try:
        par.expr = expression
        mode_cls = type(par.mode)
        par.mode = getattr(mode_cls, "EXPRESSION", par.mode)
        value = _json_safe(par.eval() if hasattr(par, "eval") else par.val)
        err = None
    except Exception as e:  # noqa: BLE001
        value = None
        err = f"{type(e).__name__}: {e}"
    try:
        after = [str(e) for e in getattr(node, "errors", []) or []]
    except Exception:  # noqa: BLE001
        after = []
    new_errors = [e for e in after if e not in before]
    return {"ok": True, "path": node.path, "param": param,
            "expression": expression, "mode": str(par.mode),
            "value": value, "error": err, "op_errors": new_errors}


def _do_get_errors(path, include_warnings=True):
    """Diagnostics under a node, split by severity (tdmcp #146).

    ``errors`` stays a flat list of error strings for backwards compatibility;
    warnings are reported separately with counts so a caller cannot mistake a
    warning for an error (or miss it entirely, as before).
    """
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    errors = list(getattr(node, "errors", []) or [])
    warnings = list(getattr(node, "warnings", []) or []) if include_warnings else []
    split = split_diagnostics(errors, warnings)
    return {"ok": True,
            "errors": [str(e) for e in errors],
            "warnings": [str(w) for w in warnings],
            "diagnostics": split["diagnostics"],
            "total": split["total"],
            "error_count": split["error_count"],
            "warning_count": split["warning_count"]}


def _do_execute_python(code):
    if not ALLOW_EXEC:
        return {"ok": False, "error": "execute_python is disabled (TD_MCP_ALLOW_EXEC=0 / TD_MCP_EXEC_MODE=off)"}
    for name in _EXEC_FORBIDDEN:
        if name in code:
            return {"ok": False, "error": f"execute_python forbids {name!r}"}
    # Lint BEFORE running: a worker-thread run() does not raise, so the only
    # moment we can warn is the write itself (Embody v6.0.251).
    warning = threading_warning(code, "execute_python")
    ns = {"op": op, "project": project, "me": me, "parent": parent}
    exec(code, ns)  # noqa: S102 - intentional in-TD RCE, gated by host/exec flag
    value = ns.get("__result__", ns.get("result", None))
    out = {"ok": True, "exec_mode": EXEC_MODE}
    out.update(_serialize_exec_result(value))
    if warning:
        out["warning"] = warning
    return out


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
    if len(params) > MAX_PARAMS:
        return {"ok": False,
                "error": f"too many parameters: {len(params)} > {MAX_PARAMS} "
                          "(TD_MCP_MAX_PARAMS)"}
    if not ALLOW_EXEC:
        sources = code_bearing_sources(op_type=getattr(node, "OPType", None),
                                       params=params)
        if sources:
            return code_route_denied("set_parameters", sources)
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


# Methods that only ever recompute or reset an operator -- safe to keep
# available when exec is off, since none of them evaluate caller text.
_SAFE_NODE_METHODS = frozenset((
    "cook", "reset", "pulse", "destroy", "save", "openViewer", "closeViewer",
    "resetPars", "copy", "collapseSelected",
))


def _do_exec_node_method(path, method, args=None):
    """Call a method on a node (e.g. cook, reset, pulse a parameter)."""
    if not ALLOW_EXEC and method not in _SAFE_NODE_METHODS:
        return code_route_denied("exec_node_method",
                                 ["node method %s" % (method,)])
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
    if not ALLOW_EXEC:
        sources = []
        for spec in nodes:
            if not isinstance(spec, dict):
                continue
            found = code_bearing_sources(op_type=spec.get("type"),
                                         params=spec.get("params"))
            label = spec.get("name")
            sources.extend("%s on %s" % (f, label) if label else f
                           for f in found)
        if sources:
            return code_route_denied("import_recipe", sources[:10])
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
    # app.version still reports the legacy generation ("099"); the public
    # year.build identifier lives on app.build (dcc-mcp #5). Version-compat
    # checks compare against "2023.11000"-style builds, so they need the
    # latter -- td_build was previously unusable for that.
    return {"ok": True,
            "td_build": touchdesigner_release(getattr(app, "version", None),
                                              getattr(app, "build", None)),
            "td_version": str(getattr(app, "version", "")),
            "bridge_version": BRIDGE_VERSION,
            "fps": project.cook.rate,
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
    if len(ops) > MAX_BATCH_SIZE:
        return {"ok": False,
                "error": f"batch too large: {len(ops)} > {MAX_BATCH_SIZE} "
                          "(TD_MCP_MAX_BATCH)"}
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
            "exec_mode": EXEC_MODE, "bridge_version": BRIDGE_VERSION,
            "protected_paths": PROTECTED, "tools": sorted(_DISPATCH.keys())}


# --- data readers (benoitliard) -------------------------------------------
def _do_read_chop(path, channel=None, samples=10):
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    samples = _cap(samples, MAX_SAMPLES)
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
    rows = _cap(rows, MAX_ROWS)
    data = []
    for r in range(min(rows, node.numRows)):
        data.append([node[r, c].val for c in range(node.numCols)])
    return {"ok": True, "rows": data}


def _do_set_dat_text(path, text, expected_sha256=None):
    """Set a DAT's text, optionally guarded by an expected SHA-256.

    Optimistic concurrency (dcc-mcp pattern): pass the SHA-256 of the DAT's
    current text in ``expected_sha256``. If it doesn't match, the write is
    refused so concurrent edits can't clobber each other.
    """
    if not ALLOW_EXEC:
        return code_route_denied("set_dat_text", ["DAT text"])
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    encoded = text.encode("utf-8") if isinstance(text, str) else text
    if len(encoded) > MAX_DAT_BYTES:
        return {"ok": False,
                "error": f"DAT text too large: {len(encoded)} > {MAX_DAT_BYTES} bytes "
                          "(TD_MCP_MAX_DAT_BYTES)"}
    if expected_sha256:
        current = getattr(node, "text", "") or ""
        current_bytes = current.encode("utf-8") if isinstance(current, str) else current
        actual = hashlib.sha256(current_bytes).hexdigest()
        if not hmac.compare_digest(actual, str(expected_sha256).lower()):
            return {"ok": False,
                    "error": "DAT text changed since the expected_sha256 was "
                             "computed; re-read and retry",
                    "expected_sha256": str(expected_sha256).lower(),
                    "actual_sha256": actual}
    warning = threading_warning(text if isinstance(text, str) else "",
                                "set_dat_text %s" % (path,))
    try:
        node.text = text
        new_sha = hashlib.sha256(encoded).hexdigest()
        out = {"ok": True, "path": node.path, "sha256": new_sha}
        if warning:
            out["warning"] = warning
        return out
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


_DISPATCH = {
    "create_node": lambda a: _do_create(a.get("path"), a.get("type"), a.get("name"),
                                         a.get("nodeX"), a.get("nodeY")),
    "delete_node": lambda a: _do_delete(a.get("path")),
    "set_parameters": lambda a: _do_set_parameters(a.get("path"), a.get("params", {}), a.get("detailLevel", "normal")),
    "get_parameters": lambda a: _do_get_parameters(a.get("path"), a.get("detailLevel", "normal")),
    "get_parameter_info": lambda a: _do_get_parameter_info(a.get("path"), a.get("names")),
    "bind_parameter_expression": lambda a: _do_bind_parameter_expression(a.get("path"), a.get("param"), a.get("expression")),
    "get_errors": lambda a: _do_get_errors(a.get("path"), a.get("include_warnings", True)),
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
    "set_dat_text": lambda a: _do_set_dat_text(a.get("path"), a.get("text", ""), a.get("expected_sha256")),
    "scan_network": lambda a: _do_scan_network(a.get("path"), _cap(a.get("depth", 3), MAX_DEPTH)),
    "build_and_verify": lambda a: _do_build_and_verify(a.get("path"), a.get("type"), a.get("name"), a.get("params")),
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
    "find_nodes": lambda a: _do_find_nodes(a.get("path"), a.get("query"), a.get("type"), _cap(a.get("depth", 4), MAX_DEPTH)),
    "set_node_position": lambda a: _do_set_node_position(a.get("path"), a.get("x", 0), a.get("y", 0)),
    "timeline": lambda a: _do_timeline(a.get("action"), a.get("value")),
    "export_recipe": lambda a: _do_export_recipe(a.get("path"), _cap(a.get("depth", 3), MAX_DEPTH)),
    "import_recipe": lambda a: _do_import_recipe(a.get("recipe"), a.get("target_parent", "*here")),
    "save_tox": lambda a: _do_save_tox(a.get("path"), a.get("file_path")),
}


def _redact_paths(text):
    """Redact user paths and the bridge's own home dir from error messages.

    TDPilot H-1: ``/Users/<user>/...`` → ``~/...`` and ``~/.td_mcp/...`` →
    ``<TD_MCP_HOME>/...`` so 500s don't leak the operator's username or repo
    layout to an agent log.
    """
    if not text:
        return text
    out = str(text)
    home = os.path.expanduser("~")
    if home and home not in ("", "/"):
        out = out.replace(home, "~")
    mcp_home = os.path.join("~", ".td_mcp").replace("\\", "/")
    out = out.replace(os.path.join(os.path.expanduser("~"), ".td_mcp").replace("\\", "/"),
                      "<TD_MCP_HOME>")
    out = out.replace(mcp_home, "<TD_MCP_HOME>")
    return out


def _wrap(result):
    """Attach recovery hints to any failed result, redacting user paths."""
    if isinstance(result, dict) and result.get("ok") is False:
        if "recovery_hints" not in result:
            hints = recovery_hints(result.get("error", ""))
            if hints:
                result["recovery_hints"] = hints
        if result.get("error"):
            result["error"] = _redact_paths(result["error"])
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

    # ---------------------------------------------------------------------------
    # JSON-RPC 2.0 (MCP Streamable HTTP) handling
    # ---------------------------------------------------------------------------
    @staticmethod
    def _jsonrpc_result(req_id, result):
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _jsonrpc_error(req_id, code, message):
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    def _handle_jsonrpc(self, payload, session_id, session):
        """Process a JSON-RPC request (or batch) into response envelope(s).

        Returns:
          * None for notifications (no HTTP body is written -> 202 Accepted)
          * a single response dict for a request
          * a list of response dicts for a batch
        """
        if isinstance(payload, list):
            out = []
            for item in payload:
                r = self._handle_jsonrpc(item, session_id, session)
                if r is not None:
                    out.append(r)
            return out if out else None

        if not isinstance(payload, dict):
            return None

        method = payload.get("method")
        req_id = payload.get("id")
        params = payload.get("params") or {}

        # Notifications carry no id and expect no response.
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return self._jsonrpc_result(req_id, {
                "protocolVersion": "2026-07-28",
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "serverInfo": {"name": "td-mcp-bridge"},
            })
        if method == "ping":
            return self._jsonrpc_result(req_id, {})
        if method == "tools/list":
            tools = [
                {
                    "name": name,
                    "description": f"Bridge tool: {name}",
                    "inputSchema": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": True,
                    },
                }
                for name in _DISPATCH
            ]
            return self._jsonrpc_result(req_id, {"tools": tools})
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            fn = _DISPATCH.get(name)
            if fn is None:
                return self._jsonrpc_error(req_id, -32601, f"unknown tool: {name}")
            try:
                result = _wrap(fn(arguments))
            except Exception as e:  # pragma: no cover - handlers return dicts
                result = {"ok": False, "error": str(e)}
            return self._jsonrpc_result(req_id, {
                "content": [
                    {"type": "text", "text": json.dumps(result, indent=2, default=str)}
                ]
            })
        if method == "resources/list":
            return self._jsonrpc_result(req_id, {"resources": []})
        if method == "resources/read":
            return self._jsonrpc_result(req_id, {"content": []})
        if method == "prompts/list":
            return self._jsonrpc_result(req_id, {"prompts": []})
        return self._jsonrpc_error(req_id, -32601, f"method not found: {method}")

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

        # Process JSON-RPC (may be a single request, a batch, or a notification)
        response = self._handle_jsonrpc(payload, self._current_session_id, session)
        if response is None:
            # Notification (e.g. notifications/initialized): accept, no body.
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Mcp-Session-Id", self._current_session_id)
            self.end_headers()
        elif isinstance(response, list):
            self._send_jsonrpc_response(None, result=response)
        else:
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
            "auth_token": AUTH_TOKEN, "allow_exec": ALLOW_EXEC,
            "exec_mode": EXEC_MODE}
