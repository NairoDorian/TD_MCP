"""td_mcp_bridge — the MCP server that runs inside TouchDesigner.

Verify every change here in a real TouchDesigner before trusting it:
`python scripts/td_live.py deploy` then `python scripts/live_smoke.py`
(AGENTS.md). Most of the defects fixed in this file were invisible to the
unit suite and obvious in the first live call.

Do not paste this by hand. Run the builder once in the Textport and it creates
a `/td_mcp` component wired to load this file:

    TD_MCP_REPO = r'C:/path/to/TD_MCP'
    exec(open(TD_MCP_REPO + '/bridge/bootstrap.py').read())

That gives you a Text DAT synced to this file (edit and TouchDesigner reloads),
an Execute DAT that starts it on Start *and* Create, and a parameter page for
port / token / exec gate. `TD_MCP_SAVE_TOX = 1` also emits a drag-and-drop
`td_mcp.tox`. See docs/TOUCHDESIGNER_COMPONENT.md.

Manual control, if you need it:
    Start:  op('/project1/td_mcp/bridge').module.start()
    Stop:   op('/project1/td_mcp/bridge').module.stop()

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

Every tool call runs inside a named undo block (ui.undo.startBlock /
endBlock) so one Ctrl+Z reverts a whole agent batch.
"""

import ast as _ast
import base64
import difflib
import fnmatch
import hashlib
import hmac
import json
import math
import os
import queue
import re
import secrets
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote

# The bridge imports NOTHING from td_mcp. It runs inside TouchDesigner as a
# Text DAT, where this package is not on sys.path, so any such import fails --
# and until v1.12.0 one did. `from td_mcp.streamable_http import
# StreamableHTTPMixin` sat in a try/except that set a flag on failure, and the
# handler class then inherited from the missing name unconditionally:
#
#     NameError: name 'StreamableHTTPMixin' is not defined
#
# The bridge had never compiled inside TouchDesigner. Nothing noticed because
# pytest runs with td_mcp importable; the double now blocks it (see
# tests/td_double.py::block_td_mcp) and scripts/check_bridge_stale.py refuses
# the import outright. The mixin contributed nothing in any case -- the handler
# below overrides every method it defined -- but the handler DID call three
# session helpers that were neither imported nor defined. They live here now.

# --- MCP sessions (Streamable HTTP `Mcp-Session-Id`) ------------------------
_sessions = {}
_sessions_lock = threading.Lock()
SESSION_TTL = 3600.0


def _generate_session_id():
    return secrets.token_urlsafe(16)


def _purge_sessions(now=None):
    """Drop sessions idle past SESSION_TTL. Runs on every touch, so the store
    stays bounded without a sweeper thread to start, stop and forget."""
    now = time.time() if now is None else now
    with _sessions_lock:
        stale = [sid for sid, s in _sessions.items()
                 if now - s.get("last_seen", 0.0) > SESSION_TTL]
        for sid in stale:
            del _sessions[sid]
    return len(stale)


def _touch_session(session_id, auth_token=None):
    now = time.time()
    _purge_sessions(now)
    with _sessions_lock:
        session = _sessions.get(session_id)
        if session is None:
            session = {"created": now, "auth_token": auth_token}
            _sessions[session_id] = session
        session["last_seen"] = now
        return session


def _get_session(session_id):
    with _sessions_lock:
        session = _sessions.get(session_id)
    if session is None:
        return None
    if time.time() - session.get("last_seen", 0.0) > SESSION_TTL:
        with _sessions_lock:
            _sessions.pop(session_id, None)
        return None
    return session


def _locate_bridge_dir():
    """The folder this bridge was loaded from, so the chat UI beside it loads.

    `__file__` is unreliable here. Inside a Text DAT it is the DAT's *operator
    path* (`/project1/td_mcp/bridge`), not a file, and abspath() turns that
    into a folder that never existed. Prefer the DAT's own File parameter (set
    in link mode), then a `__file__` that actually exists on disk, else give up
    quietly -- only `GET /` (the chat UI) depends on it.
    """
    try:
        dat_file = str(me.par.file.eval())  # noqa: F821 - TD global
        if dat_file and os.path.isfile(dat_file):
            return os.path.dirname(os.path.abspath(dat_file))
    except Exception:  # noqa: BLE001
        pass
    try:
        if os.path.isfile(__file__):  # noqa: F821 - undefined under exec()
            return os.path.dirname(os.path.abspath(__file__))  # noqa: F821
    except Exception:  # noqa: BLE001
        pass
    return ""


# Path to the chat UI HTML (beside this file when running in TD)
def _chat_ui_path():
    """Resolved on first use, never at import: module-level code runs while
    the DAT is still compiling, and reading a TD object there is how 1.13.3's
    first build broke (see _STARTED_AT). tests/test_bridge_wave13.py enforces it."""
    return os.path.join(_locate_bridge_dir(), "chat_ui.html")

# WebSocket connected clients set (for future push events)
_ws_clients = set()
_ws_lock = threading.Lock()
_sse_clients = set()


def debug(*args):
    """Bridge log helper (used by start/stop); overridable by the host."""
    print("[td_mcp]", *args)


class _undo_block:
    """``with _undo_block(name):`` -- one TouchDesigner undo step per tool call.

    TD's ``ui.undo`` is NOT a context manager. It is an ``Undo`` object with
    ``startBlock(name)`` / ``endBlock()`` (Undo_Class). The bridge shipped
    ``with ui.undo:`` at every dispatch site, so every tool call -- read-only
    ones included -- died with "'td.Undo' object does not support the context
    manager protocol" before reaching its handler. The test double had
    modelled ui.undo as a context manager, which is why 665 tests disagreed
    with the first real session.

    Tolerant on purpose: a host without ``ui.undo`` still runs the tool; it
    just gets no undo step.
    """

    def __init__(self, name="td_mcp"):
        self.name = "td_mcp: %s" % name if name else "td_mcp"
        self._open = False

    def __enter__(self):
        try:
            ui.undo.startBlock(self.name)  # noqa: F821 - TD global
            self._open = True
        except Exception:  # noqa: BLE001
            self._open = False
        return self

    def __exit__(self, *exc):
        if self._open:
            try:
                ui.undo.endBlock()  # noqa: F821
            except Exception:  # noqa: BLE001
                pass
        return False


def _orphaned_servers(port):
    """Servers on `port` started by a *previous* load of this DAT.

    In link mode TouchDesigner recompiles this module whenever the file on
    disk changes -- but the old module's ``serve_forever`` thread keeps
    running and keeps the port. The new module's ``_server`` is None, so
    ``stop()`` would do nothing and ``start()`` would fail with "address
    already in use". The server thread is the durable handle: its target is
    the bound method ``server.serve_forever``, so the server is reachable
    through ``threading.enumerate()`` without touching TD storage (which is
    saved into the .toe and must not hold a socket).
    """
    if not port:
        return []
    found = []
    for t in threading.enumerate():
        srv = getattr(getattr(t, "_target", None), "__self__", None)
        if isinstance(srv, HTTPServer) and srv is not _server:
            try:
                if srv.server_address[1] == port:
                    found.append(srv)
            except Exception:  # noqa: BLE001
                continue
    return found


def _shutdown_server(srv):
    for step in (srv.shutdown, srv.server_close):
        try:
            step()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Main-thread marshalling
#
# The HTTP server runs on its own thread. TouchDesigner objects cannot be
# touched from any thread but the main one -- the first live session got
# "TouchDesigner objects cannot be accessed outside the main thread" on every
# call and a dialog warning that the application "may behave unpredictably or
# terminate". Nor is `run()` an answer: called off the main thread it does not
# raise, it corrupts TD silently (Embody v6.0.251, Derivative-confirmed).
#
# The sanctioned pattern is a queue drained from the main thread. The HTTP
# thread enqueues a job and waits; the component's `startup` Execute DAT calls
# `pump()` on every Frame Start and runs the job there. Latency is at most one
# frame. Nothing here imports anything beyond the standard library.
# ---------------------------------------------------------------------------
_JOBS = queue.Queue()
_MAIN_THREAD = threading.main_thread()
_last_pump = 0.0
#: `root.time.play` as of the last pump. The HTTP thread may not read TD, so
#: the pump leaves this behind for the stall diagnosis -- a paused timeline is
#: by far the most likely reason frames stopped, and the fix is one keystroke.
_last_pump_playing = None
#: How long a request waits for the main thread when the pump is healthy.
MAIN_THREAD_TIMEOUT = float(os.environ.get("TD_MCP_MAIN_THREAD_TIMEOUT", "30"))
#: Jobs per frame are bounded by time, not count, so one slow capture cannot
#: be followed by a second in the same frame (Embody: keep sweeps under ~8ms).
PUMP_BUDGET_MS = float(os.environ.get("TD_MCP_PUMP_BUDGET_MS", "8"))
#: A pump that has not ticked for this long is stalled or absent; requests
#: then fail fast with a diagnosis instead of waiting MAIN_THREAD_TIMEOUT.
PUMP_STALL_SECONDS = 2.0


def on_main_thread():
    return threading.current_thread() is _MAIN_THREAD


class _Job:
    __slots__ = ("fn", "args", "kwargs", "done", "result", "error")

    def __init__(self, fn, args, kwargs):
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.done = threading.Event()
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001 - re-raised on the caller's thread
            self.error = exc
        finally:
            self.done.set()


def _pump_diagnosis():
    if not _last_pump:
        return ("the main-thread pump has never run. The component's `startup` "
                "Execute DAT must have Frame Start on and call "
                "op('bridge').module.pump() -- re-run bridge/bootstrap.py to "
                "rebuild it.")
    age = time.time() - _last_pump
    if _last_pump_playing is False:
        return ("the TouchDesigner timeline is PAUSED, so no frames are "
                "starting and the main-thread pump (Frame Start) has not run "
                "for %.1fs. Press Play in TouchDesigner and this request will "
                "be serviced immediately." % age)
    return ("the main-thread pump last ran %.1fs ago -- TouchDesigner's main "
            "thread is stalled (a modal dialog or a long cook), the timeline "
            "was paused, or this bridge was replaced by a reload of the DAT: "
            "press Play, or pulse Restart Bridge on the component." % age)


def _call_on_main(fn, *args, **kwargs):
    """Run ``fn`` on TouchDesigner's main thread and return its result.

    Inline when already there (a tool calling a tool, a Textport call); via
    the job queue from anywhere else. Raises TimeoutError with a diagnosis
    when nothing is draining the queue.
    """
    if on_main_thread():
        return fn(*args, **kwargs)
    job = _Job(fn, args, kwargs)
    stalled = (not _last_pump) or (time.time() - _last_pump > PUMP_STALL_SECONDS)
    _JOBS.put(job)
    if not job.done.wait(PUMP_STALL_SECONDS if stalled else MAIN_THREAD_TIMEOUT):
        raise TimeoutError("main thread did not service the request: "
                           + _pump_diagnosis())
    if job.error is not None:
        raise job.error
    return job.result


def pump():
    """Drain queued jobs. Called on every frame by the component's Execute DAT.

    Whichever thread calls this *is* the main thread as far as the bridge is
    concerned -- TouchDesigner runs Execute DAT callbacks on its main thread,
    and the test double's frame loop stands in for it.
    """
    global _MAIN_THREAD, _last_pump, _last_pump_playing
    _MAIN_THREAD = threading.current_thread()
    _last_pump = time.time()
    try:
        _last_pump_playing = bool(root.time.play)  # noqa: F821 - TD global
    except Exception:  # noqa: BLE001
        _last_pump_playing = None
    deadline = time.perf_counter() + PUMP_BUDGET_MS / 1000.0
    ran = 0
    while True:
        try:
            job = _JOBS.get_nowait()
        except queue.Empty:
            break
        job.run()
        ran += 1
        if time.perf_counter() >= deadline:
            break
    return ran


#: Mutating tools and the argument naming what they change. ``True`` also
#: fences the bridge's ancestors (deleting /project1 deletes the bridge).
#: Cosmetic tools (position, colour, comment, layout) are deliberately absent.
_SELF_GUARDED = {
    "create_node": ("path", False),
    "delete_node": ("path", True),
    "set_parameters": ("path", False),
    "pulse_parameter": ("path", False),
    "bind_parameter_expression": ("path", False),
    "set_dat_text": ("path", False),
    "rename_node": ("path", False),
    "set_flags": ("path", True),
    "exec_node_method": ("path", True),
    "copy_node": ("new_parent", False),
    "connect_nodes": ("to_path", False),
    "disconnect_nodes": ("to_path", False),
    "import_recipe": ("target_parent", False),
    "build_network": ("target_parent", False),
    "restore_network": ("target_parent", False),
    "edit_custom_parameters": ("path", False),
}


def _guard_call(tool, args):
    spec = _SELF_GUARDED.get(tool)
    if spec is None:
        return None
    key, ancestors = spec
    target = (args or {}).get(key)
    if not target:
        return None  # path-less defaults never resolve into the bridge
    return _self_guard(target, tool, ancestors=ancestors)


def _run_tool(tool, args):
    """Execute one tool inside its undo block. Main thread only."""
    fn = _DISPATCH.get(tool)
    if fn is None:
        return {"ok": False, "error": f"unknown tool: {tool}"}
    refused = _guard_call(tool, args)
    if refused is not None:
        return _wrap(refused)
    with _undo_block(tool):
        return _wrap(fn(args or {}))


def _invoke(tool, args):
    """Run a tool on the main thread, whatever thread this is called from.

    A multi-frame tool (non-TOP capture) instead runs here on the calling
    thread and marshals each step itself -- it has to let frames pass.
    """
    multi = _MULTI_FRAME_TOOLS.get(tool)
    if multi is not None and not on_main_thread():
        return multi(args or {})
    return _call_on_main(_run_tool, tool, args or {})


def _tool_description(name):
    """First-line docstring of the ``_do_*`` handler backing dispatch key *name*.

    The dispatch lambdas each call a single ``_do_*`` function; we find it by
    scanning the lambda's ``co_names`` for a ``_do_`` global, then read that
    function's docstring.  This keeps descriptions in one place (the handler
    docstrings) instead of a second hardcoded table that drifts.  Falls back to
    a placeholder when no docstring is found.
    """
    lam = _DISPATCH.get(name)
    if lam is not None:
        for ref in lam.__code__.co_names:
            if ref.startswith("_do_"):
                fn = globals().get(ref)
                if fn is not None and fn.__doc__:
                    return fn.__doc__.strip().split("\n", 1)[0].strip()
    return "Bridge tool: " + name


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
BRIDGE_VERSION = "1.13.3"


#: time.time() when this module's server started listening. `scripts/td_live.py
#: deploy` waits for it to change: a new value proves the server was restarted
#: AFTER the file copy, so it runs the new code. (A source hash read from the
#: DAT at import time was tried first and broke the module: never touch TD
#: objects at module level -- the import runs before the network is ready.)
_STARTED_AT = None

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
MAX_BODY_BYTES = int(os.environ.get("TD_MCP_MAX_BODY_BYTES", str(32 * 1024 * 1024)))


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

#: Builtins `execute_python` refuses even when exec is enabled. Matched as
#: *names*, never as substrings -- see `forbidden_builtins`.
FORBIDDEN_BUILTINS = ("__import__", "open", "exec", "eval", "compile",
                      "globals", "locals")

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


def forbidden_builtins(code, forbidden=None):
    """Names from `forbidden` that `code` uses as **builtins**, not attributes.

    The check used to be ``if name in code`` -- a substring test over the raw
    source. ``par.eval()`` is the single most common call in TouchDesigner
    Python, and it contains "eval", so `execute_python` refused most
    legitimate scripts: reading any parameter, ``str(d.par.file.eval())``,
    a variable called ``retrieval``, even the word "open" in a comment. It
    cost two silent failures in one live session before anyone noticed the
    error text.

    Substring matching was also never a real defence -- ``getattr(__builtins__,
    'ev' + 'al')`` walks straight past it. This parses instead and matches a
    bare ``Name``, which is what a builtin call actually looks like:
    ``eval(...)`` and ``f = eval`` are caught; ``par.eval()`` and ``'eval'``
    in a string are not.

    A guard-rail against an obvious mistake, not a sandbox. With exec enabled,
    a caller holding the token can reach anything TouchDesigner can --
    ``docs/SECURITY.md`` says so plainly.

    Returns a sorted list of offending names. Raises SyntaxError for code that
    does not parse, so the caller can report that rather than run it.
    """
    forbidden = set(forbidden if forbidden is not None else FORBIDDEN_BUILTINS)
    tree = _ast.parse(code)
    hits = set()
    for node in _ast.walk(tree):
        # A bare name in any load/store position: eval(...), f = eval,
        # [eval][0](). An ast.Attribute (par.eval) is deliberately not a Name,
        # which is the whole distinction this function exists to make.
        if isinstance(node, _ast.Name) and node.id in forbidden:
            hits.add(node.id)
    return sorted(hits)


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


def group_diagnostics(text):
    """Split one of TD's diagnostic strings into individual messages.

    ``errors()``/``warnings()``/``scriptErrors()`` each return a *single*
    newline-joined string (``OP_Class``), not a list. Splitting naively on
    newlines turns one Python traceback into a dozen "errors", which is how
    a single failing ``onCook`` comes back looking like a catastrophe.

    A traceback is therefore kept whole: it opens at
    ``Traceback (most recent call last):`` and closes on the next
    non-indented line, which is the exception line that terminates it
    (Embody v6.0.265 -- "counting one traceback as one error rather than one
    per line").
    """
    if not text:
        return []
    lines = [ln.rstrip() for ln in str(text).splitlines() if ln.strip()]
    groups, current, in_traceback = [], [], False
    for line in lines:
        indented = line[:1].isspace()
        if not indented and not in_traceback and current:
            groups.append("\n".join(current))
            current = []
        current.append(line)
        if "Traceback (most recent call last)" in line:
            in_traceback = True
        elif in_traceback and not indented:
            in_traceback = False
            groups.append("\n".join(current))
            current = []
    if current:
        groups.append("\n".join(current))
    return groups


# A docked Info DAT carries plenty of routine text (resolution, format, cook
# time). Only lines that actually read as a compile failure are diagnostics.
_SHADER_ERROR_HINT = re.compile(
    r"\berror\b|\bfailed\b|\bundeclared\b|\bunresolved\b|\bsyntax\b|"
    r"\bcannot\b|\binvalid\b|\breserved\b", re.I)


def shader_error_lines(info_text):
    """Compile errors out of a GLSL operator's docked Info DAT text.

    ``op.errors()`` reports "(none)" for a shader that failed to compile: TD
    emits only a vague warning and writes the real message to the auto-docked
    Info DAT (Embody v6.0.264). Blank lines and the DAT's routine
    ``key: value`` telemetry are dropped so a healthy shader yields ``[]``.
    """
    out = []
    for line in group_diagnostics(info_text):
        first = line.splitlines()[0]
        if not _SHADER_ERROR_HINT.search(first):
            continue
        # "Cook Time: 0.4" style telemetry can contain the word "error" only
        # as part of a key like "Errors: 0" -- a zero count is not an error.
        if re.match(r"^\s*\w[\w ]*:\s*0\s*$", first):
            continue
        out.append(line)
    return out


#: Severity for each diagnostic kind. Script and shader failures are what the
#: network paints red, so they count as errors, not as a softer third thing.
_DIAGNOSTIC_SEVERITY = {
    "cook": "error",
    "script": "error",
    "shader": "error",
    "warning": "warning",
}


def split_diagnostics(errors, warnings, script_errors=None, shader_errors=None):
    """Every diagnostic as one severity- and kind-tagged list, plus counts.

    tdmcp #146: the bridge used to return only ``errors``, so a caller
    counting them read warnings as errors (or never saw them at all).

    Wave 12: TouchDesigner reports on *three* surfaces --- ``errors()``,
    ``warnings()`` and ``scriptErrors()`` --- plus GLSL compile text that
    appears on none of them. Reading two of the four meant an operator could
    sit red in the network while this bridge called it clean. ``kind``
    distinguishes them; ``severity`` keeps the old two-way split intact.
    """
    items = []
    for kind, messages in (("cook", errors),
                           ("script", script_errors),
                           ("shader", shader_errors),
                           ("warning", warnings)):
        for m in messages or []:
            items.append({"message": str(m),
                          "severity": _DIAGNOSTIC_SEVERITY[kind],
                          "kind": kind})
    return {
        "diagnostics": items,
        "total": len(items),
        "error_count": sum(1 for i in items if i["severity"] == "error"),
        "warning_count": sum(1 for i in items if i["severity"] == "warning"),
    }

# --- Data reduction (Embody's "never a blind dump" contract) ---------------
#
# A read tool exists to answer "what did this chain do to my data", and a raw
# dump answers that badly: it costs a caller thousands of tokens to look at
# 4,096 numbers and conclude "the values are between 0 and 1". Every reader
# below reduces by default and offers the raw form only on request.


def summarize_series(values, precision=4):
    """min/max/mean/std over a numeric series, skipping non-finite entries.

    Population standard deviation (the ``numpy`` default), so a single-sample
    channel reports ``std: 0.0`` rather than dividing by zero.
    """
    nums = []
    for v in values or []:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            nums.append(f)
    if not nums:
        return {"count": 0, "min": None, "max": None, "mean": None, "std": None}
    n = len(nums)
    mean = sum(nums) / n
    variance = sum((x - mean) ** 2 for x in nums) / n
    return {"count": n,
            "min": round(min(nums), precision),
            "max": round(max(nums), precision),
            "mean": round(mean, precision),
            "std": round(math.sqrt(variance), precision)}


def summarize_column(cells, max_sample=5):
    """Per-column stats for a DAT column.

    A fully numeric column reduces to a stats block; anything else reports how
    many distinct values it holds plus a short sample, which is what a caller
    actually needs to know about a table of names or paths.
    """
    values = ["" if c is None else str(c) for c in (cells or [])]
    out = {"count": len(values),
           "empty": sum(1 for v in values if not v.strip())}
    if not values:
        return out
    stats = summarize_series(values)
    if stats["count"] == len(values):
        out["numeric"] = stats
        return out
    seen, sample = set(), []
    for v in values:
        if v not in seen:
            seen.add(v)
            if len(sample) < max_sample:
                sample.append(v)
    out["distinct"] = len(seen)
    out["sample"] = sample
    return out


def edge_rows(rows, edge=3):
    """First and last `edge` rows, with a marker for what was elided.

    Measured by Embody at ~17x smaller than a full dump for a real table, and
    it keeps the two things a caller reads a table for -- the header and the
    shape of the data -- instead of the middle nobody looks at.
    """
    rows = list(rows or [])
    if len(rows) <= edge * 2:
        return {"rows": rows, "elided": 0}
    return {"rows": rows[:edge] + rows[-edge:],
            "elided": len(rows) - edge * 2,
            "note": f"{len(rows) - edge * 2} middle rows elided; "
                    f"pass format='rows' for the full table"}


# --- TDN capture policy (mirrors td_mcp/tdn; the bridge cannot import it) --
#
# The bridge is pasted into a Text DAT, so it cannot import td_mcp. These two
# policies therefore live in both places and must agree; the offline module
# `td_mcp/tdn/__init__.py` is the authority and carries the full rationale.

def suggest_parameter(name, available, cutoff=0.6):
    """The closest real parameter name to `name`, or None.

    TouchDesigner parameter names are case-sensitive and inconsistent between
    built-ins (lowercase: `period`) and custom parameters (capitalised:
    `Port`), so a case-insensitive pass runs after the exact-case one --
    `port` should find `Port`.
    """
    candidates = [str(c) for c in (available or [])]
    if not name or not candidates:
        return None
    hit = difflib.get_close_matches(str(name), candidates, n=1, cutoff=cutoff)
    if hit:
        return hit[0]
    lowered = {c.lower(): c for c in candidates}
    hit = difflib.get_close_matches(str(name).lower(), list(lowered), n=1,
                                    cutoff=cutoff)
    return lowered[hit[0]] if hit else None


#: Mean per-tile luminance change below which consecutive frames are "the same
#: picture". Comfortably above sampling jitter, well below any real animation.
STATIC_THRESHOLD = 1.0


def motion_verdict(signatures, threshold=STATIC_THRESHOLD):
    """Whether a sequence of frame signatures shows any movement.

    Mirrors ``td_mcp.vision.motion_verdict`` (the bridge cannot import it).
    A single-frame verdict cannot tell "renders correctly" from "renders
    correctly and is frozen" -- the most common silent failure in a feedback
    or audio-reactive network. Fewer than two usable frames yields
    ``is_static: None``: unknown, not false, because claiming a network
    animates on the strength of one frame is the error being fixed.
    """
    usable = [s for s in signatures or [] if s]
    if len(usable) < 2:
        return {"frames": len(usable), "is_static": None,
                "max_delta": None, "mean_delta": None,
                "caption": "not enough frames to judge motion"}
    width = min(len(s) for s in usable)
    deltas = []
    for a, b in zip(usable, usable[1:]):
        deltas.append(sum(abs(a[i] - b[i]) for i in range(width)) / width)
    max_delta = max(deltas)
    mean_delta = sum(deltas) / len(deltas)
    is_static = max_delta < threshold
    return {
        "frames": len(usable),
        "is_static": is_static,
        "max_delta": round(max_delta, 4),
        "mean_delta": round(mean_delta, 4),
        "caption": (f"Viewport is static across {len(usable)} frames - the "
                    f"render is frozen (max tile delta {max_delta:.3f})."
                    if is_static else
                    f"Viewport is animating across {len(usable)} frames "
                    f"(max tile delta {max_delta:.3f})."),
    }


#: Operator family suffixes, longest first so `POP` cannot shadow `TOP`.
_OP_FAMILIES = ("COMP", "CHOP", "SOP", "TOP", "MAT", "DAT", "POP")


def normalize_op_type(op_type):
    """A TouchDesigner opType string from either spelling of an operator name.

    ``COMP.create`` takes a *type*, and the only string it accepts is the
    camel form: ``noiseTOP``, ``moviefileinTOP``, ``glslmultiTOP``. The
    documentation and this project's own corpus use display names --
    ``Noise TOP``, ``Movie File In TOP``, ``GLSL Multi TOP`` -- and passing one
    of those to ``create`` fails as an unknown type. Lowercase every word,
    concatenate, and restore the family suffix in the case TD expects.

    An unrecognised string is returned unchanged: this normalizes, it does not
    validate, and TD's own error message for a genuinely unknown type is
    better than any this could invent.
    """
    if not isinstance(op_type, str):
        return op_type
    text = op_type.strip()
    if not text:
        return text
    parts = text.split()
    if len(parts) > 1:
        family = parts[-1].upper()
        if family in _OP_FAMILIES:
            return "".join(p.lower() for p in parts[:-1]) + family
        return "".join(parts)
    for family in _OP_FAMILIES:
        if text.upper().endswith(family) and len(text) > len(family):
            # TD class names have an all-lowercase prefix: 'NoiseTOP' -> 'noiseTOP'.
            return text[:-len(family)].lower().replace("_", "") + family
    return text


#: Tag prefix marking one parameter as runtime state, not configuration.
EXCLUDE_TAG_PREFIX = "tdn_exclude:"


def excluded_parameter_names(tags):
    """Parameter names an operator's tags mark as runtime state.

    ``tdn_exclude:Port`` means "export this operator, its wiring and its other
    parameters, but not this parameter's constant value" -- for a negotiated
    port, a session file path, a live readout. The tag lives on the operator so
    it reaches parameters on non-COMP operators, which is exactly where `Port`
    and `file` live.
    """
    out = []
    for tag in tags or []:
        text = str(tag)
        if text.startswith(EXCLUDE_TAG_PREFIX):
            name = text[len(EXCLUDE_TAG_PREFIX):].strip()
            if name:
                out.append(name)
    return out


def dat_text_policy(text, file_backed=False, externalized=False):
    """Whether a DAT's text is embedded in an export. ``(embed, source)``.

    "Skip what a file already holds", never "throw authored code away": if the
    content lives in a file that travels with the project, embedding only
    duplicates it; if it does not, the export is the ONLY copy. Any doubt
    embeds -- a wrong call here loses a shader or a callback permanently.
    """
    if not text:
        return False, "empty"
    if file_backed or externalized:
        return False, "file"
    return True, "embedded"


# --- MCP protocol version negotiation ---------------------------------------
#
# Lived in td_mcp/streamable_http.py until v1.12.0 -- a module the bridge could
# not import inside TouchDesigner and whose `_handle_jsonrpc` the bridge's own
# copy shadowed anyway, so the negotiation there never ran. It runs here.

#: Newest first: every version whose request/response surface this transport
#: implements. None of them requires a feature it lacks.
SUPPORTED_PROTOCOL_VERSIONS = ("2026-07-28", "2025-06-18", "2024-11-05")

#: MCP: a server that does not support the requested version "SHOULD respond
#: with the latest version it supports" -- the newest, not the oldest.
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]


def negotiate_protocol_version(requested):
    """The protocol version to answer an `initialize` with.

    Echo the client's version when we speak it; otherwise answer with our
    newest and let the client decide. Returns ``(version, is_exact_match)``.
    """
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested, True
    return DEFAULT_PROTOCOL_VERSION, False


def server_capabilities(protocol_version=None):
    """Capabilities to advertise: exactly the methods `_handle_jsonrpc` serves.

    No `logging` -- the bridge implements no `logging/setLevel`, and
    advertising a capability that is not there invites a call that fails.
    """
    return {"tools": {}, "resources": {}, "prompts": {}}


# ===========================================================================
# END PURE HELPERS
# ===========================================================================

# Recovery-hint rules (Embody-style): (compiled regex, cause, action, next_tools)
RECOVERY_HINT_RULES = [
    # Specific rules first: matching is first-hit, and the generic `par\b`
    # rule below used to claim the forbidden-builtin error because its text
    # mentions `par.eval()`.
    (re.compile(r"forbids the builtin", re.I),
     "execute_python refused a builtin it blocks (open/eval/exec/compile/"
     "globals/locals/__import__).",
     "Use import statements, pathlib.Path(...).read_text()/write_text(), and "
     "TD's own methods (op(), par.eval()); set optional inputs with try/except "
     "NameError instead of globals().",
     ["execute_python"]),
    (re.compile(r"TD_MCP_ALLOW_EXEC|TD_MCP_EXEC_MODE|code-bearing", re.I),
     "This bridge runs with code execution disabled.",
     "Use the typed tools (create_node, set_parameters, connect_nodes) or ask "
     "the user to enable exec on the td_mcp component.",
     ["describe_td_tools", "set_parameters"]),
    (re.compile(r"protected|bridge component|self[_ ]path", re.I),
     "The target is the TD_MCP bridge itself (or a protected path).",
     "Work in /project1 or another COMP; the bridge refuses edits that would "
     "cut the connection you are using.",
     ["list_nodes"]),
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
     "Read the exact names with describe_op_type (type) or "
     "get_parameter_info (existing node) before setting values.",
     ["describe_op_type", "get_parameter_info"]),
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


def _current_parent():
    """TD's global ``parent()`` -- the COMP this bridge DAT lives in.

    Reached through a helper on purpose. Three handlers used to read
    ``parent = _op(path) or parent()``: assigning ``parent`` makes it local
    for the whole function, so the fallback call raised UnboundLocalError.
    Only on the error path -- a valid path short-circuits the ``or`` -- which
    is how it survived. A bad parent path now says "parent not found".

    And not ``parent()`` itself: evaluated in this DAT that is the bridge's own
    COMP (``/project1/td_mcp``), so every path-less ``create_node`` landed
    inside the bridge. The default is the network the user is looking at
    (``*here``), never the bridge, else ``/project1``.
    """
    home = _bridge_home()
    node = _op("*here")
    if node is not None and _inside_bridge(node.path, home):
        node = None
    if node is None:
        node = op("/project1")  # noqa: F821 - TD global
    return node if node is not None else parent()  # noqa: F821


def _inside_bridge(path, home=None):
    home = _bridge_home() if home is None else home
    if not home or home == "/" or not path:
        return False
    return path == home or path.startswith(home + "/")


def _op_type(node):
    """``OP.OPType`` ('noiseTOP'): the name ``COMP.create()`` accepts.

    ``OP.type`` is the family-less short form ('noise') and cannot rebuild an
    operator -- a recipe that recorded it failed on import with "Unknown
    operator type. Value:'constant'".
    """
    return getattr(node, "OPType", None) or getattr(node, "type", "")


def _int_of(node, name, default=0):
    """An int attribute that some operator families expose as a method."""
    value = getattr(node, name, default)
    if callable(value):
        try:
            value = value()
        except Exception:  # noqa: BLE001
            return default
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _input_sources(node):
    """``[(input_index, upstream_op, upstream_output_index), ...]``.

    Connector_Class: a connector's ``connections`` is the list of *connectors*
    on the far end, and the operator there is ``connector.owner``. There is no
    ``outputOP``/``inputOP``; reading those raised AttributeError inside a
    bare except at six sites, so every wiring read -- get_node,
    get_connections, export_recipe, map_network -- came back empty in the
    first live session that got that far.
    """
    out = []
    try:
        for i, ic in enumerate(node.inputConnectors):
            for c in (ic.connections or []):
                owner = getattr(c, "owner", None)
                if owner is not None:
                    out.append((i, owner, int(getattr(c, "index", 0) or 0)))
    except Exception:  # noqa: BLE001
        pass
    return out


def _output_targets(node):
    """``[(output_index, downstream_op, downstream_input_index), ...]``."""
    out = []
    try:
        for i, oc in enumerate(node.outputConnectors):
            for c in (oc.connections or []):
                owner = getattr(c, "owner", None)
                if owner is not None:
                    out.append((i, owner, int(getattr(c, "index", 0) or 0)))
    except Exception:  # noqa: BLE001
        pass
    return out


def _is_protected(path):
    if not PROTECTED:
        return False
    node = _op(path)
    for p in PROTECTED:
        if path == p or (node is not None and node.path.startswith(p + "/")):
            return True
    return False


def _bridge_home():
    """Path of the COMP this bridge DAT lives in (``/project1/td_mcp``), or None
    outside TouchDesigner."""
    try:
        home = me.parent()  # noqa: F821 - TD global: the bridge Text DAT
        return home.path if home is not None else None
    except Exception:  # noqa: BLE001
        return None


def _self_guard(path, tool, ancestors=False):
    """Refuse a mutation aimed at the bridge itself.

    Derivative's TDMCP 1.1.57 fences every mutating tool off its own component
    after finding `set_parameters` could switch its authentication off; Embody
    v6.2.49 refuses a call that would destroy its own host. Same rule here: the
    connection the agent is using must not be editable through itself.
    ``ancestors=True`` (delete) also refuses a parent of the bridge -- deleting
    ``/project1`` deletes the server answering the call. Read-only tools are not
    fenced; ``execute_python`` is not either, and is documented as such.
    """
    home = _bridge_home()
    if home == "/":
        home = None  # a bridge loaded bare (no component) has no home to fence
    node = _op(path) if path else None
    target = node.path if node is not None else (path or "")
    if home and target:
        inside = _inside_bridge(target, home)
        above = ancestors and (target == "/" or home.startswith(target.rstrip("/") + "/"))
        if inside or above:
            return {"ok": False, "error_code": "self_path",
                    "error": "%s refused: %s %s the TD_MCP bridge component (%s); "
                             "editing it would cut this connection"
                             % (tool, target, "contains" if above and not inside else "is inside", home)}
    if _is_protected(target or path):
        return {"ok": False, "error_code": "protected",
                "error": "%s refused: path is protected: %s" % (tool, target or path)}
    return None


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
    """Create a node of the given operator type at ``path`` (or *here*)."""
    if not ALLOW_EXEC:
        sources = code_bearing_sources(op_type=type)
        if sources:
            return code_route_denied("create_node", sources)
    parent = _op(path) if path else _current_parent()
    if parent is None:
        return {"ok": False, "error": f"parent not found: {path}"}
    # COMP.create(opType, name) -- type FIRST. The arguments were reversed
    # here, so every create asked TouchDesigner for an operator whose type was
    # the caller's chosen *name*; with no name given it asked for a type of
    # "TOP", the last word of the display name. `create_node` and everything
    # built on it (build_and_verify, import_recipe, the scaffolds) could not
    # have worked against a real TouchDesigner.
    op_type = normalize_op_type(type)
    node = parent.create(op_type, name) if name else parent.create(op_type)
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
    """Delete the node at ``path`` (refuses protected paths)."""
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


def _par_mode_name(par):
    mode = getattr(par, "mode", None)
    name = getattr(mode, "name", None) or str(mode or "")
    return name.split(".")[-1].upper()


def _par_reading(par):
    """``(value, mode_info_or_None)`` for one parameter, read the way TD shows it.

    ``Par.val`` is the CONSTANT slot only: verified on 2025.33230, a parameter
    whose expression is ``1.5 + 1.5`` reads ``val`` 1.0 and ``eval()`` 3.0, and
    a bound one reads its stale constant instead of its master. So the value is
    ``eval()``; the mode, expression, bind master or export source ride along
    for anything not CONSTANT (Embody v6.3.8, issue #201); and an expression
    that cannot evaluate reports ``<eval error: ...>`` instead of failing the
    whole read.
    """
    mode = _par_mode_name(par)
    info = None
    try:
        value = _json_safe(par.eval())
    except Exception as exc:  # noqa: BLE001 - a broken expression is data
        value = "<eval error: %s>" % str(exc)[:160]
        info = {"mode": mode, "eval_error": True}
    if mode and mode != "CONSTANT":
        info = dict(info or {}, mode=mode)
        if mode == "EXPRESSION":
            info["expr"] = str(getattr(par, "expr", "") or "")
        elif mode == "BIND":
            info["bindExpr"] = str(getattr(par, "bindExpr", "") or "")
            master = getattr(par, "bindMaster", None)
            if master is not None:
                owner = getattr(master, "owner", None)
                info["bindMaster"] = ("%s:%s" % (owner.path, master.name)
                                      if owner is not None and hasattr(master, "name")
                                      else str(master))
        elif mode == "EXPORT":
            src = getattr(par, "exportSource", None)
            if src is not None:
                info["exportSource"] = _json_safe(src)
            exp_op = getattr(par, "exportOP", None)
            if exp_op is not None:
                info["exportOP"] = getattr(exp_op, "path", str(exp_op))
    return value, info


def _do_get_parameters(path, detail="normal", names=None, non_default=False):
    """Read a node's parameter values as TD shows them (eval, not the constant slot).

    ``names``: comma list or glob(s) to filter; ``non_default``: only values
    that differ from the operator's defaults (the token-efficient read). Up to
    40 by default; all on detailLevel='full'. Non-constant parameters are also
    described under ``modes`` (expression / bind master / export source).
    """
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    pars = list(node.par)
    if names:
        pats = [n.strip() for n in (names.split(",") if isinstance(names, str) else names)
                if str(n).strip()]
        pars = [p for p in pars if any(fnmatch.fnmatchcase(p.name, pat) for pat in pats)]
    if non_default:
        pars = [p for p in pars if not getattr(p, "isDefault", False)]
    params, modes = {}, {}
    for p in pars:
        value, info = _par_reading(p)
        params[p.name] = value
        if info:
            modes[p.name] = info
    total = len(params)
    if detail == "brief":
        return {"ok": True, "count": total, "non_constant": len(modes)}
    if detail != "full" and total > 40:
        keep = list(params)[:40]
        params = {k: params[k] for k in keep}
        modes = {k: v for k, v in modes.items() if k in params}
    out = {"ok": True, "params": params}
    if modes:
        out["modes"] = modes
    if len(params) < total:
        out["truncated"] = True
        out["total"] = total
        out["hint"] = "pass detailLevel='full', a names filter, or non_default=true"
    return out


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
            # The expression IS the configuration; `value` is one momentary
            # reading of it. Without this an agent could see mode=EXPRESSION
            # and had no way to learn, preserve or repair the expression.
            "expr": str(getattr(par, "expr", "") or ""),
            "bindExpr": str(getattr(par, "bindExpr", "") or ""),
            "isDefault": bool(getattr(par, "isDefault", False)),
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
    # A bad expression shows up as a *new* cook error (or a new traceback on a
    # DAT's callback), so snapshot before and diff after. This used to read
    # `node.errors` as an attribute inside a bare except, so `before` and
    # `after` were both always `[]` -- `op_errors` could never be non-empty and
    # the whole "eval-error reporting" contract was dead on arrival.
    before = _node_errors(node)
    try:
        par.expr = expression
        mode_cls = type(par.mode)
        par.mode = getattr(mode_cls, "EXPRESSION", par.mode)
        value = _json_safe(par.eval() if hasattr(par, "eval") else par.val)
        err = None
    except Exception as e:  # noqa: BLE001
        value = None
        err = f"{type(e).__name__}: {e}"
    after = _node_errors(node)
    new_errors = [e for e in after if e not in before]
    return {"ok": True, "path": node.path, "param": param,
            "expression": expression, "mode": str(par.mode),
            "value": value, "error": err, "op_errors": new_errors}


def _diag_call(node, method_name, recurse=False):
    """Call one of TD's diagnostic accessors and split it into messages.

    ``errors``/``warnings``/``scriptErrors`` are **methods returning str**
    (``OP_Class``), not iterable attributes. Reading them as attributes yields
    a bound method: truthy, so an ``or []`` fallback never fires, and
    ``list(...)`` raises ``TypeError: 'method' object is not iterable``. That
    is what this bridge did at six call sites, so ``get_errors`` always failed
    and every other reader swallowed the exception and reported a clean
    network. Call, don't index.
    """
    accessor = getattr(node, method_name, None)
    if not callable(accessor):
        return []
    try:
        return group_diagnostics(accessor(recurse=recurse))
    except Exception:  # noqa: BLE001 - a diagnostic read must never mask the
        return []      # diagnostic it was called to fetch


def _info_dat_for(node):
    """The Info DAT docked to `node`, or None.

    Discovery is by ``dat.type == 'info'``, never by name: TouchDesigner keeps
    the OLD dock name after the host is renamed, so name matching silently
    stops working exactly when a project gets tidied up (Embody v6.0.264).
    """
    for docked in (getattr(node, "docked", None) or []):
        try:
            if docked.type == "info":
                return docked
        except Exception:  # noqa: BLE001
            continue
    return None


def _shader_errors_for(node):
    """GLSL compile errors for `node`, read from its docked Info DAT.

    Reading an Info DAT **cooks** it, so this is never run project-wide off a
    write: Embody measured a whole-project shader pass at 3.36s against a
    0.25s budget, which tripped a one-way disable latch and killed the feature
    for the session. Callers scope it to the operators a write actually
    touched; an explicit ``get_errors`` is a deliberate request and may walk.
    """
    info = _info_dat_for(node)
    if info is None:
        return []
    try:
        return shader_error_lines(info.text)
    except Exception:  # noqa: BLE001
        return []


def _diag_host(node):
    """The operator a diagnostic belongs to.

    A GLSL TOP's shader lives in a docked child (``<name>_pixel``); editing it
    must still report the *host's* compile error, so walk `dock` up to the
    operator that owns the failure.
    """
    seen = 0
    while getattr(node, "dock", None) is not None and seen < 8:
        node = node.dock
        seen += 1
    return node


def _op_diagnostics(node, include_warnings=True, include_shader=True,
                    recurse=False):
    """All four of TouchDesigner's diagnostic surfaces for one operator.

    ``errors()`` / ``warnings()`` / ``scriptErrors()`` are the three the API
    exposes; GLSL compile failures appear on none of them and are read from
    the docked Info DAT. ``errorCount``/``hasErrors`` therefore mean "what the
    network shows red" rather than "what two of four surfaces admit to".
    """
    errors = _diag_call(node, "errors", recurse)
    warnings = _diag_call(node, "warnings", recurse) if include_warnings else []
    script = _diag_call(node, "scriptErrors", recurse)
    shader = _shader_errors_for(node) if include_shader else []
    split = split_diagnostics(errors, warnings, script, shader)
    return {
        "errors": errors + script + shader,
        "cook_errors": errors,
        "script_errors": script,
        "shader_errors": shader,
        "warnings": warnings,
        "diagnostics": split["diagnostics"],
        "total": split["total"],
        "error_count": split["error_count"],
        "warning_count": split["warning_count"],
        "errorCount": split["error_count"],
        "hasErrors": split["error_count"] > 0,
    }


def _node_errors(node):
    """Flat error list for a node -- cook + script + shader, never a raise.

    The read-only scanners (`scan_network`, `get_node`, `validate_network`)
    call this. They used to inline ``[str(e) for e in node.errors]`` inside a
    bare ``except: pass``, which turned the TypeError above into silence:
    every one of them reported zero errors for every network, forever.
    """
    if node is None:
        return []
    try:
        diag = _op_diagnostics(node, include_warnings=False,
                               include_shader=False)
        return diag["errors"]
    except Exception:  # noqa: BLE001
        return []


def _do_get_errors(path, include_warnings=True, include_shader=True,
                   recurse=False):
    """Diagnostics for a node, split by severity and kind (tdmcp #146).

    ``errors`` stays a flat list of error strings for backwards compatibility
    -- now correctly populated, and including script tracebacks and GLSL
    compile failures, because those are what the network paints red. Warnings
    are reported separately with counts so a caller cannot mistake a warning
    for an error (or miss it entirely, as before).
    """
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    out = {"ok": True, "path": node.path}
    out.update(_op_diagnostics(node, include_warnings=include_warnings,
                               include_shader=include_shader,
                               recurse=bool(recurse)))
    return out


def _do_clear_script_errors(path, recurse=False):
    """Clear script errors on a node (``OP.clearScriptErrors``).

    Script errors persist until explicitly cleared, so a single cook inside an
    extension re-init window -- where the extension object is briefly the
    previous one -- badges a node long after the code is correct again
    (Embody v6.0.265). Without this, an agent that has already fixed the code
    sees a stale traceback and "fixes" working code.
    """
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    before = _diag_call(node, "scriptErrors", bool(recurse))
    fn = getattr(node, "clearScriptErrors", None)
    if not callable(fn):
        return {"ok": False, "error": "operator has no clearScriptErrors"}
    fn(recurse=bool(recurse))
    after = _diag_call(node, "scriptErrors", bool(recurse))
    return {"ok": True, "path": node.path, "cleared": len(before) - len(after),
            "remaining": after}


def _do_execute_python(code):
    """Execute arbitrary Python in the TouchDesigner main thread (gated by the exec flag)."""
    if not ALLOW_EXEC:
        return {"ok": False, "error": "execute_python is disabled (TD_MCP_ALLOW_EXEC=0 / TD_MCP_EXEC_MODE=off)"}
    try:
        offenders = forbidden_builtins(code)
    except SyntaxError as e:
        return {"ok": False, "error": f"could not parse the submitted Python: {e}"}
    if offenders:
        return {"ok": False,
                "error": "execute_python forbids the builtin(s) %s. Note this "
                         "matches the BUILTIN only -- TouchDesigner methods "
                         "that share the name, like par.eval(), are fine."
                         % ", ".join(repr(n) for n in offenders),
                "forbidden": offenders}
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
    """List the children of a node (defaults to the root COMP when path is empty)."""
    node = _op(path) or root
    if node is None:
        return {"ok": False, "error": "not found"}
    kids = [{"name": c.name, "type": c.type, "op_type": _op_type(c), "path": c.path}
            for c in node.children]
    if detail == "brief":
        return {"ok": True, "count": len(kids)}
    return {"ok": True, "nodes": kids}


def _do_scan_network(path, depth=3):
    """Traverse a subnetwork: every node, connection, and non-default parameter."""
    start_node = _op(path) or root
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
        errors = _node_errors(node)
        scanned.append({
            "path": node.path,
            "name": node.name,
            "type": node.type,
            "op_type": _op_type(node),
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
    # The shader pass is scoped to the one operator this call created, so the
    # Info DAT cook it costs is bounded -- never the project-wide sweep that
    # blew Embody's write-effect budget.
    errs = _do_get_errors(node_path)
    vp = _do_capture_viewport(node_path, "brief")
    return {
        "ok": True,
        "path": node_path,
        "errors": errs.get("errors", []),
        "script_errors": errs.get("script_errors", []),
        "shader_errors": errs.get("shader_errors", []),
        "warnings": errs.get("warnings", []),
        "errorCount": errs.get("errorCount", 0),
        "hasErrors": errs.get("hasErrors", False),
        "diagnostics": errs.get("diagnostics", []),
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
    node = _op(path) or root
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
    non_defaults, modes = {}, {}
    try:
        for p in node.par:
            if not p.isDefault:
                # eval(), not val: val is the stale constant slot (see _par_reading)
                non_defaults[p.name], info = _par_reading(p)
                if info:
                    modes[p.name] = info
    except Exception:  # noqa: BLE001
        pass
    inputs = [src.path for _i, src, _o in _input_sources(node)]
    outputs = [dst.path for _o, dst, _i in _output_targets(node)]
    errors = _node_errors(node)
    flags = {}
    for flag in ("display", "render", "bypass", "lock", "viewer", "allowCooking"):
        try:
            value = getattr(node, flag)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(value, bool):
            flags[flag] = value
    out = {
        "ok": True,
        "name": node.name,
        "type": node.type,
        "op_type": _op_type(node),
        "path": node.path,
        "inputs": inputs,
        "outputs": outputs,
        "params": non_defaults,
        "errors": errors,
        "position": [getattr(node, 'nodeX', 0), getattr(node, 'nodeY', 0)],
        # Layout needs the real tile size: spacing by a fixed step overlaps
        # wide tiles (COMPs), so placement is nodeX + nodeWidth + gap.
        "size": [getattr(node, 'nodeWidth', None), getattr(node, 'nodeHeight', None)],
        "flags": flags,
    }
    if modes:
        out["modes"] = modes
    return out


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
    """Set one or more parameters on a node; reports unknown params and suggestions."""
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
    notes = {}
    # An unknown parameter used to be a silent `continue`, so a misspelling
    # came back as {"ok": true, "applied": {}} and the caller carried on
    # believing the network was configured. Report it, and say what was meant.
    unknown = {}
    all_pars = list(node.par)
    available = [p.name for p in all_pars]
    tuplets = {}
    for p in all_pars:
        tn = getattr(p, "tupletName", None)
        if tn and tn != p.name:
            tuplets.setdefault(tn, []).append(p)
    # A tuplet name with a list value: resolution=[1280, 720] -> resolutionw/h.
    # (2025.33230 has no OP.parTuple; Par.tuplet/tupletName carry the grouping.)
    expanded = {}
    for k, v in params.items():
        if node.par[k] is None and k in tuplets and isinstance(v, (list, tuple)):
            for comp, cv in zip(tuplets[k], v):
                expanded[comp.name] = cv
            notes[k] = "tuplet expanded to %s" % ", ".join(
                c.name for c in tuplets[k][:len(v)])
        else:
            expanded[k] = v
    params = expanded
    for k in params:
        if node.par[k] is None:
            unknown[k] = suggest_parameter(k, available)
    invalid = {}
    for k, v in params.items():
        if node.par[k] is None:
            continue
        par = node.par[k]
        try:
            was_mode = _par_mode_name(par)
            if isinstance(v, dict):
                if "expr" in v:
                    par.expr = v["expr"]
                if v.get("pulse"):
                    par.pulse()
                if "val" in v:
                    par.val = v["val"]
                applied[k] = v
            elif getattr(par, "isPulse", False):
                # Writing True to a pulse par is accepted and does nothing.
                if v:
                    par.pulse()
                applied[k] = "pulsed" if v else "not pulsed (false)"
            else:
                token = _menu_token(par, v)
                if token is not None and token != v:
                    notes[k] = {"menu_label": "%r -> token %r" % (v, token)}
                    v = token
                par.val = v
                applied[k] = v
                note = _write_readback(par, v, was_mode)
                if note is not None:
                    if note.pop("invalid", False):
                        invalid[k] = note
                    else:
                        notes[k] = note
        except Exception as e:  # noqa: BLE001
            applied[k] = f"error: {e}"
    if invalid:
        # TD does not raise on a bad menu token: the parameter just keeps its
        # old value. Treated as the failure it is, with the valid tokens.
        return {"ok": False,
                "error": "invalid menu value(s) on %s: %s" % (
                    getattr(node, "OPType", node.type),
                    "; ".join("%s=%r (valid: %s)" % (k, params[k], ", ".join(n["menu"][:12]))
                              for k, n in invalid.items())),
                "invalid": invalid, "applied": applied}
    if unknown:
        # Partial application is a failure, not a success with a footnote: the
        # caller has to know the network is not in the state it asked for.
        detail_lines = []
        for name, guess in sorted(unknown.items()):
            detail_lines.append("%s (did you mean %s?)" % (name, guess)
                                if guess else name)
        out = {"ok": False,
               "error": "unknown parameter(s) on %s: %s"
                        % (getattr(node, "OPType", node.type),
                           ", ".join(detail_lines)),
               "unknown": unknown,
               "applied": applied,
               "available_count": len(available)}
        if detail == "full":
            out["available"] = sorted(available)
        return out
    if detail == "brief":
        out = {"ok": True, "applied_count": len(applied)}
    else:
        out = {"ok": True, "applied": applied}
    if notes:
        out["notes"] = notes
    return out


def _menu_token(par, value):
    """A menu *label* ('Clamp [0-1]') or case variant mapped to its token
    ('clamp'); None when ``par`` is not a menu or nothing matches."""
    if not getattr(par, "isMenu", False) or not isinstance(value, str):
        return None
    names = [str(n) for n in (getattr(par, "menuNames", None) or [])]
    if value in names:
        return value
    labels = [str(n) for n in (getattr(par, "menuLabels", None) or [])]
    low = value.strip().lower()
    for i, name in enumerate(names):
        if name.lower() == low or (i < len(labels) and labels[i].strip().lower() == low):
            return name
    return None


def _write_readback(par, requested, was_mode):
    """What TD actually did with a plain-value write, when it is not what was
    asked. Verified on 2025.33230: a bad menu token is silently ignored; a
    value past a hard clamp is silently clamped (resolutionw=-5 -> 1); a write
    to an EXPRESSION/BIND parameter switches it to CONSTANT and drops the
    expression. Returns None when the write landed as asked."""
    try:
        got = par.eval()
    except Exception:  # noqa: BLE001
        return None
    if getattr(par, "isMenu", False):
        names = [str(n) for n in (getattr(par, "menuNames", None) or [])]
        if isinstance(requested, str) and names and requested not in names:
            return {"invalid": True, "kept": _json_safe(got), "menu": names}
        return None
    note = {}
    if was_mode not in ("", "CONSTANT"):
        note["mode_changed"] = "%s -> CONSTANT (the old expression/bind was discarded)" % was_mode
    if (isinstance(requested, (int, float)) and not isinstance(requested, bool)
            and isinstance(got, (int, float)) and not isinstance(got, bool)
            and abs(float(got) - float(requested)) > 1e-9):
        note["clamped_to"] = _json_safe(got)
    return note or None


_OPTYPE_CACHE = {}
_DESCRIBE_NUMERIC = {"Float", "Int", "XY", "XYZ", "XYZW", "WH", "UV", "UVW", "RGB", "RGBA"}
_DESCRIBE_REFS = {"OP", "COMP", "OBJ", "PanelCOMP", "TOP", "CHOP", "SOP", "DAT",
                  "MAT", "POP", "Object", "Python"}


def _describe_par(p):
    style = str(getattr(p, "style", ""))
    try:
        created = p.val if style in _DESCRIBE_REFS else p.eval()
    except Exception:  # noqa: BLE001
        created = p.val
    row = {"name": p.name, "label": getattr(p, "label", p.name), "style": style,
           "page": p.page.name if getattr(p, "page", None) is not None else None,
           "default": _json_safe(created)}
    try:
        declared = _json_safe(p.default)
        if declared != row["default"]:
            row["declared_default"] = declared
    except Exception:  # noqa: BLE001
        pass
    tn = getattr(p, "tupletName", None)
    if tn and tn != p.name:
        row["tuplet"] = tn
    if getattr(p, "isMenu", False):
        row["menu"] = [str(n) for n in (p.menuNames or [])]
        labels = [str(n) for n in (p.menuLabels or [])]
        if labels and labels != row["menu"]:
            row["menu_labels"] = labels
    if style in _DESCRIBE_NUMERIC:
        lo = _json_safe(p.min) if getattr(p, "clampMin", False) else None
        hi = _json_safe(p.max) if getattr(p, "clampMax", False) else None
        if lo is not None or hi is not None:
            row["clamp"] = [lo, hi]
    if getattr(p, "readOnly", False):
        row["read_only"] = True
    return row


def _do_describe_op_type(op_type, pattern=None, page=None, include_menus=True):
    """Parameter names, labels, styles, creation defaults and menus of an
    operator TYPE, read off a throwaway instance in /sys/quiet (cooking off,
    outside the project) and cached per type for the session.

    The look-before-you-guess read (Embody v6.2.65, Derivative TDMCP
    ``get_help``). ``default`` is the value a fresh operator holds, because
    ``Par.default`` lies for some menus (Text DAT ``language`` declares
    ``input``, a new DAT holds ``text`` -- verified 2025.33230).
    """
    name = normalize_op_type(op_type or "")
    cls = None
    try:
        import td as _td
        cls = getattr(_td, name, None) if name[:1].islower() else None
    except Exception:  # noqa: BLE001 - outside TD (the double)
        _td = None
    if cls is None and _td is not None:
        known = [n for n in dir(_td) if n[:1].islower()
                 and n.endswith(("TOP", "CHOP", "SOP", "POP", "DAT", "MAT", "COMP"))]
        return {"ok": False, "error": "unknown operator type %r" % op_type,
                "did_you_mean": difflib.get_close_matches(name, known, n=5, cutoff=0.6),
                "hint": "Use the TD class name: noiseTOP, lfoCHOP, gridPOP, textDAT."}
    cached = name in _OPTYPE_CACHE
    if not cached:
        quiet = op("/sys/quiet")  # noqa: F821 - TD global
        if quiet is None:
            return {"ok": False, "error": "/sys/quiet is not available; create the "
                    "operator and use get_parameter_info instead"}
        probe = None
        try:
            probe = quiet.create(cls if cls is not None else name, "td_mcp_describe")
            _OPTYPE_CACHE[name] = {
                "family": getattr(probe, "family", ""),
                "label": getattr(probe, "label", ""),
                "inputs": [_int_of(probe, "minInputs"), _int_of(probe, "maxInputs")],
                "pages": [pg.name for pg in getattr(probe, "pages", [])],
                "parameters": [_describe_par(p) for p in probe.par
                               if not (p.name == "pageindex" and getattr(p, "page", None) is None)],
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "could not instantiate %s: %s" % (name, exc)}
        finally:
            if probe is not None:
                try:
                    probe.destroy()
                except Exception:  # noqa: BLE001
                    pass
    entry = _OPTYPE_CACHE[name]
    rows = entry["parameters"]
    if page:
        rows = [r for r in rows if (r.get("page") or "").lower() == page.lower()]
    if pattern:
        pat = pattern.lower()
        if not any(ch in pat for ch in "*?["):
            pat = "*%s*" % pat
        rows = [r for r in rows if fnmatch.fnmatchcase(r["name"].lower(), pat)
                or fnmatch.fnmatchcase((r.get("label") or "").lower(), pat)]
    if not include_menus:
        rows = [{k: v for k, v in r.items() if k not in ("menu", "menu_labels")} for r in rows]
    out = {"ok": True, "op_type": name, "family": entry["family"],
           "display_name": ("%s %s" % (entry["label"], entry["family"])).strip(),
           "inputs": entry["inputs"], "pages": entry["pages"], "count": len(rows),
           "total": len(entry["parameters"]), "parameters": rows, "cached": cached}
    if (pattern or page) and not rows:
        out["hint"] = ("No parameter matched -- the name guess is wrong, not the "
                       "operator. Retry without filters and match by label.")
    return out


def _do_pulse_parameter(path, param):
    """Pulse a pulse-type parameter (reset, reload, start...). Setting True on
    one is accepted by TD and does nothing; this calls ``par.pulse()``."""
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    par = node.par[param] if param else None
    if par is None:
        pulses = [p.name for p in node.par if getattr(p, "isPulse", False)]
        return {"ok": False, "error": "unknown parameter %r on %s" % (param, node.path),
                "pulse_parameters": pulses}
    if not getattr(par, "isPulse", False) and not getattr(par, "isMomentary", False):
        return {"ok": False, "error": "%s.%s is a %s parameter, not a pulse"
                % (node.path, param, getattr(par, "style", "?"))}
    par.pulse()
    return {"ok": True, "path": node.path, "pulsed": param}


def _do_disconnect_nodes(from_path, to_path, to_input=0):
    """Break the wire from from_path into to_path's input[to_input]."""
    src = _op(from_path)
    dst = _op(to_path)
    if dst is None:
        return {"ok": False, "error": f"destination not found: {to_path}"}
    try:
        target = src.path if src else None
        ic = dst.inputConnectors[to_input]
        # Disconnecting the INPUT connector drops exactly this wire. The old
        # code disconnected the far-end connector, which is the upstream
        # OUTPUT and would drop every wire that output feeds -- and it never
        # got that far, because it read `.outputOP` on it first.
        wired_from = [getattr(c, "owner", None) for c in (ic.connections or [])]
        if target is not None and not any(o is not None and o.path == target
                                          for o in wired_from):
            return {"ok": False,
                    "error": f"{to_path}[{to_input}] is not fed by {from_path}",
                    "fed_by": [o.path for o in wired_from if o is not None]}
        ic.disconnect()
        return {"ok": True, "disconnected": f"{from_path} -x-> {to_path}[{to_input}]"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _do_get_connections(path):
    """Return a normalized wiring map (inputs + outputs) for a node."""
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    ins = [{"input": i, "from": src.path, "from_output": oi}
           for i, src, oi in _input_sources(node)]
    outs = [{"output": o, "to": dst.path, "to_input": ii}
            for o, dst, ii in _output_targets(node)]
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
    parent = _op(target_parent) if target_parent else _current_parent()
    if parent is None:
        return {"ok": False, "error": "parent not found"}
    if not snapshot or not os.path.exists(snapshot):
        return {"ok": False, "error": f"snapshot not found: {snapshot}"}
    try:
        loaded = parent.loadTox(snapshot)
        return {"ok": True, "path": loaded.path}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _now_abs_frame():
    try:
        return int(absTime.frame)  # noqa: F821 - TD global
    except Exception:  # noqa: BLE001
        return None


def _do_get_performance(path, max_age_frames=10, depth=1, limit=25):
    """Rank cook times (ms, CPU+GPU) to find what is slowing a network down.

    ``cookTime`` is the duration of the LAST cook, however long ago: an
    operator that cooked once at creation would top the list forever
    (Derivative TDMCP #14). Anything that has not cooked within
    ``max_age_frames`` (from ``cookAbsFrame`` vs ``absTime.frame``) is marked
    ``stale``, gets no budget share and sorts after the live cooks.
    ``budget_pct`` is CPU+GPU against one frame at the project cook rate.
    A non-COMP path profiles that one operator.
    """
    start = _op(path) if path else root  # noqa: F821
    if start is None:
        return {"ok": False, "error": "not found"}
    try:
        fps = float(project.cookRate) or 60.0  # noqa: F821
    except Exception:  # noqa: BLE001
        fps = 60.0
    frame_ms = 1000.0 / fps
    now = _now_abs_frame()
    if getattr(start, "isCOMP", False):
        depth = max(1, min(int(depth or 1), MAX_DEPTH))
        try:
            nodes = start.findChildren(maxDepth=depth)
        except Exception:  # noqa: BLE001 - older doubles: direct children only
            nodes = list(getattr(start, "children", []))
    else:
        nodes = [start]
    rows, stale = [], 0
    for c in nodes:
        cpu = float(getattr(c, "cpuCookTime", 0) or 0)
        gpu = float(getattr(c, "gpuCookTime", 0) or 0)
        last = float(getattr(c, "cookTime", 0) or 0)
        row = {"path": c.path, "type": _op_type(c), "cpu_ms": round(cpu, 4),
               "gpu_ms": round(gpu, 4), "cook_ms": round(last, 4)}
        cooked_at = getattr(c, "cookAbsFrame", None)
        if now is not None and isinstance(cooked_at, (int, float)):
            age = int(now - cooked_at)
            row["frames_since_cook"] = age
            if age > int(max_age_frames):
                row["stale"] = True
                stale += 1
        if not row.get("stale"):
            row["budget_pct"] = round(100.0 * (cpu + gpu) / frame_ms, 2)
        rows.append(row)
    rows.sort(key=lambda r: (bool(r.get("stale")), -(r["cpu_ms"] + r["gpu_ms"])))
    live = [r for r in rows if not r.get("stale")]
    return {"ok": True, "fps": fps, "frame_budget_ms": round(frame_ms, 3),
            "units": "ms", "count": len(rows), "stale_count": stale,
            "live_total_budget_pct": round(sum(r.get("budget_pct", 0) for r in live), 2),
            "hotspots": live[:5], "cooks": rows[:int(limit or 25)]}


def _do_validate_network(path, depth=2):
    """Scene-contract check (TD-Codex): unplaced/overlapping nodes and cook errors."""
    start = _op(path) or root
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
        for e in _node_errors(node):
            issues.append({"severity": "error", "path": node.path,
                           "issue": f"cook error: {e}"})
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
#: Settable OP flags. `display`/`render` (what the network background and the
#: render show) and `lock` were missing, so the commonest flag change in a
#: build -- "turn the output's display flag on" -- was impossible.
_NODE_FLAGS = ("bypass", "viewer", "excludeFromCook", "allowCooking",
               "forceCooking", "cloneImmune", "pickable", "display", "render",
               "lock", "expose", "activeViewer", "current", "selected")


def _do_set_flags(path, flags=None):
    """Toggle node flags (bypass, viewer, excludeFromCook, allowCooking, ...)."""
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    flags = flags or {}
    applied = {}
    for k, v in flags.items():
        if k in _NODE_FLAGS:
            # Verified on TD 2025.33230: allowCooking=False raises on a
            # non-COMP (=True is accepted anywhere), so a mixed-type gate
            # would pass its enable pass and abort on disable. Gate a
            # non-COMP with the bypass flag or remove its demand instead.
            if k == "allowCooking" and not bool(v) and not node.isCOMP:
                applied[k] = ("skipped: allowCooking is COMP-only; gate a "
                              "non-COMP with the bypass flag or remove its "
                              "demand")
                continue
            try:
                setattr(node, k, bool(v))
                applied[k] = bool(v)
            except Exception as e:  # noqa: BLE001
                applied[k] = f"error: {e}"
    return {"ok": True, "applied": applied}


def _do_find_nodes(path, query=None, op_type=None, depth=4):
    """Search descendants by name substring and/or operator type (touch_mcp find)."""
    start = _op(path) or root
    if start is None:
        return {"ok": False, "error": "not found"}
    q = (query or "").lower()
    want_type = (op_type or "").lower()
    matches = []
    seen = set()

    def name_matches(name):
        # A query with a wildcard is a glob ("noise*"); otherwise a substring.
        # "noise*" used to be a substring test and matched nothing.
        if not q:
            return False
        if any(ch in q for ch in "*?["):
            return fnmatch.fnmatchcase(name.lower(), q)
        return q in name.lower()

    def type_matches(node):
        if not want_type:
            return False
        return (want_type in str(node.type).lower()
                or want_type in str(_op_type(node)).lower())

    def traverse(node, d):
        if node is None or d > depth:
            return
        if (name_matches(node.name) or type_matches(node)) and node.path not in seen:
            seen.add(node.path)
            matches.append({"path": node.path, "name": node.name,
                            "type": node.type, "op_type": _op_type(node)})
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


def _do_timeline(action, value=None, force=False):
    """Control the global timeline: play / pause / toggle / frame / rate / seek.

    The timeline is the Time COMP at ``root.time`` (TimeCOMP_Class: frame,
    seconds, rate, play, start, end). This used to address ``project.cook``,
    which does not exist -- every timeline call failed with "'td.Project'
    object has no attribute 'cook'" in the first live session.
    """
    try:
        tl = root.time
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"no timeline: {e}"}

    def state():
        return {"ok": True, "play": bool(tl.play), "frame": float(tl.frame),
                "seconds": float(tl.seconds), "rate": float(tl.rate),
                "start": float(tl.start), "end": float(tl.end)}

    try:
        if action in ("play", "pause", "toggle"):
            want = (True if action == "play" else
                    False if action == "pause" else not bool(tl.play))
            if not want and not force:
                # Pausing stops frames, and the main-thread pump runs on Frame
                # Start -- so a pause issued through the bridge disables the
                # bridge, including the call that would resume it. Only a
                # human at the keyboard can undo it. Learned the hard way:
                # `timeline pause` wedged a live session and cost a restart.
                return {"ok": False,
                        "error": "refusing to pause: the main-thread pump runs "
                                 "on Frame Start, so pausing the timeline stops "
                                 "this bridge answering -- including a later "
                                 "`timeline play`. Only someone at the "
                                 "TouchDesigner keyboard could resume it. Pass "
                                 "force=true if that is acceptable.",
                        "would_set_play": False,
                        "state": state()}
            tl.play = want
            return state()
        if action == "frame":
            tl.frame = float(value)
            return state()
        if action == "seek":
            tl.frame = max(float(tl.start), float(tl.frame) + float(value))
            return state()
        if action == "rate":
            tl.rate = float(value)
            return state()
        if action in (None, "", "status"):
            return state()
        return {"ok": False, "error": f"unknown timeline action: {action}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _capture_parameters(node):
    """Non-default parameters, expressions and tag-excluded names.

    A parameter in EXPRESSION or BIND mode is non-default, so a value-only
    capture writes down whatever it currently evaluates to and the rebuilt
    network is dead -- the expression is the configuration, the value is a
    momentary reading of it. Both are captured, separately.
    """
    params, expressions = {}, {}
    excluded = excluded_parameter_names(getattr(node, "tags", None))
    unknown = list(excluded)
    try:
        pars = list(node.par)
    except Exception:  # noqa: BLE001
        return params, expressions, [], []
    for p in pars:
        try:
            name = p.name
            if name in excluded:
                if name in unknown:
                    unknown.remove(name)
                # Expressions still travel: an excluded parameter is excluded
                # from its *session value*, not from its definition.
            # Par.isDefault covers value, expression AND mode, so a parameter
            # TouchDesigner itself ships with an expression (a container's
            # `resizecomp = me`) is default and stays out of the recipe.
            if p.isDefault:
                continue
            mode = str(getattr(p, "mode", "")).upper()
            expr = getattr(p, "expr", "") or ""
            if expr and ("EXPRESSION" in mode or "BIND" in mode):
                expressions[name] = expr
                continue
            if name in excluded:
                continue
            if not p.isDefault:
                params[name] = p.val
        except Exception:  # noqa: BLE001
            continue
    return params, expressions, sorted(set(excluded) - set(unknown)), sorted(unknown)


def _capture_dat_text(node):
    """A DAT's text under the embed policy. ``(text_or_None, source)``."""
    text = getattr(node, "text", None)
    if text is None:
        return None, None
    file_backed = False
    try:
        file_par = getattr(node.par, "file", None)
        file_backed = bool(file_par is not None and str(file_par.val).strip())
    except Exception:  # noqa: BLE001
        file_backed = False
    embed, source = dat_text_policy(text, file_backed=file_backed)
    return (text if embed else None), source


def _do_export_recipe(path, depth=3):
    """Serialize a subnetwork into a portable JSON blueprint (tdmcp recipe gallery).

    Records each node's name, type, non-default params, **expressions**, any
    **authored DAT text**, position and input wiring so import_recipe can
    rebuild it elsewhere.

    Two things used to be lost here. Expressions were exported as the constant
    they happened to evaluate to, and a DAT's text was not exported at all --
    so a recipe of a GLSL or callback-driven network rebuilt as an empty shell.
    Wiring was recorded as the source operator's *path* while the importer
    looked source operators up by *name*, so no wire was ever reconnected and
    nothing said so.
    """
    start = _op(path) or root
    if start is None:
        return {"ok": False, "error": "not found"}
    nodes = []
    warnings = []

    def traverse(node, d):
        if node is None or d > depth:
            return
        params, expressions, excluded, unknown = _capture_parameters(node)
        # The importer resolves sources by name.
        inputs = {str(i): src.name for i, src, _o in _input_sources(node)}
        spec = {
            "name": node.name,
            # OPType ('noiseTOP') rebuilds; the short `type` ('noise') does not.
            "type": _op_type(node),
            "family": getattr(node, "family", ""),
            "position": [getattr(node, "nodeX", 0), getattr(node, "nodeY", 0)],
            "params": params,
            "inputs": inputs,
            "children": [c.name for c in node.children],
        }
        if expressions:
            spec["expressions"] = expressions
        if excluded:
            # Visible in the artifact: a silently omitted parameter is
            # indistinguishable from one sitting at its default.
            spec["excluded_parameters"] = excluded
        if unknown:
            warnings.append(
                f"{node.path}: tdn_exclude names no such parameter: "
                + ", ".join(unknown))
        text, source = _capture_dat_text(node)
        if source:
            spec["text_source"] = source
        if text is not None:
            spec["text"] = text
        nodes.append(spec)
        try:
            for child in node.children:
                traverse(child, d + 1)
        except Exception:  # noqa: BLE001
            pass

    traverse(start, 1)
    out = {"ok": True, "root": start.path, "node_count": len(nodes),
           "recipe": nodes}
    if warnings:
        out["warnings"] = warnings
    return out


def _build_layout(specs, origin=(0, 0), step_x=200, step_y=-160):
    """Left-to-right positions by wiring depth for specs without a position."""
    by_name = {s.get("name"): s for s in specs if s.get("name")}
    depth = {}

    def depth_of(name, seen=()):
        if name in depth:
            return depth[name]
        if name in seen or name not in by_name:
            return 0  # cycle or external source
        srcs = [s for s in (by_name[name].get("inputs") or []) if s]
        d = 0 if not srcs else 1 + max(depth_of(s, seen + (name,)) for s in srcs)
        depth[name] = d
        return d

    rows, out = {}, {}
    for name in by_name:
        d = depth_of(name)
        row = rows.get(d, 0)
        rows[d] = row + 1
        out[name] = [origin[0] + d * step_x, origin[1] + row * step_y]
    return out


def _do_build_network(spec, target_parent=None, replace=False):
    """Create, configure and wire a whole network in ONE call (one Ctrl+Z).

    ``spec`` is the format ``td_validate_build`` checks offline:
    ``{"operators": [{"name", "type", "parameters": {...}, "inputs": [src|null],
    "position": [x, y], "flags": {...}, "text": "..."}], "connections": [...]}``.
    Parameters go through set_parameters (read-back, tuplets, menu tokens,
    ``{"expr": ...}``), so nothing is silently dropped the way import_recipe
    drops an unknown name. Returns a per-operator report plus the cook errors
    of everything created.
    """
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "bad spec json: %s" % exc}
    if not isinstance(spec, dict) or not isinstance(spec.get("operators"), list):
        return {"ok": False, "error": "spec must be {'operators': [...], 'connections': [...]}"}
    specs = [s for s in spec["operators"] if isinstance(s, dict)]
    if not ALLOW_EXEC:
        sources = []
        for s in specs:
            sources.extend("%s on %s" % (f, s.get("name")) for f in code_bearing_sources(
                op_type=s.get("type"), params=s.get("parameters"), text=s.get("text")))
        if sources:
            return code_route_denied("build_network", sources[:10])
    parent = _op(target_parent) if target_parent else _current_parent()
    if parent is None or not getattr(parent, "isCOMP", False):
        return {"ok": False, "error": "target_parent is not a COMP: %s" % target_parent}

    report, created, issues = {}, {}, []
    layout = _build_layout(specs)
    for s in specs:
        name, op_type = s.get("name"), s.get("type")
        if not name or not op_type:
            issues.append("operator without name/type skipped: %r" % (s,))
            continue
        existing = parent.op(name)
        if existing is not None:
            if not replace:
                report[name] = {"ok": False, "error": "already exists (pass replace=true)"}
                issues.append("%s already exists" % name)
                continue
            existing.destroy()
        try:
            node = parent.create(normalize_op_type(op_type), name)
        except Exception as exc:  # noqa: BLE001
            report[name] = {"ok": False, "error": "create failed: %s" % exc}
            issues.append("%s: create failed (%s)" % (name, exc))
            continue
        created[name] = node
        x, y = s.get("position") or layout.get(name, [0, 0])
        node.nodeX, node.nodeY = int(x), int(y)
        report[name] = {"ok": True, "path": node.path, "type": _op_type(node)}

    for s in specs:
        node = created.get(s.get("name"))
        if node is None:
            continue
        entry = report[s["name"]]
        params = s.get("parameters") or s.get("params") or {}
        if params:
            res = _do_set_parameters(node.path, params)
            if not res.get("ok"):
                entry["ok"] = False
                entry["error"] = res.get("error")
                issues.append("%s: %s" % (s["name"], res.get("error")))
            if res.get("notes"):
                entry["notes"] = res["notes"]
        if s.get("text") is not None and hasattr(node, "text"):
            try:
                node.text = s["text"]
            except Exception as exc:  # noqa: BLE001
                entry["ok"] = False
                issues.append("%s: text not set (%s)" % (s["name"], exc))
        if s.get("flags"):
            res = _do_set_flags(node.path, s["flags"])
            if not res.get("ok"):
                entry["ok"] = False
                issues.append("%s: flags (%s)" % (s["name"], res.get("error")))

    edges = []
    for s in specs:
        for i, src in enumerate(s.get("inputs") or []):
            if src:
                edges.append((src, s.get("name"), 0, i))
    for c in spec.get("connections") or []:
        edges.append((c.get("from"), c.get("to"), int(c.get("from_output", 0)),
                      int(c.get("to_input", 0))))
    wired, unwired = 0, []
    for src, dst, out_i, in_i in edges:
        a, b = created.get(src) or parent.op(src or ""), created.get(dst)
        if a is None or b is None:
            unwired.append("%s -> %s" % (src, dst))
            continue
        try:
            b.inputConnectors[in_i].connect(a.outputConnectors[out_i])
            wired += 1
        except Exception as exc:  # noqa: BLE001
            unwired.append("%s -> %s[%d]: %s" % (src, dst, in_i, exc))
    if unwired:
        issues.append("unwired: %s" % "; ".join(unwired))

    # Pull the network once so first-cook errors exist to be read: cooking
    # every leaf demands everything upstream of it.
    for node in created.values():
        if not _output_targets(node):
            _force_cook(node)
    cook_errors = {}
    for name, node in created.items():
        errs = _node_errors(node)
        if errs:
            cook_errors[name] = errs
    return {"ok": not issues, "parent": parent.path, "created": len(created),
            "wired": wired, "operators": report, "issues": issues,
            "cook_errors": cook_errors,
            "note": "one undo step: Ctrl+Z in TouchDesigner removes the whole build"}


def _do_import_recipe(recipe, target_parent="*here"):
    """Rebuild a blueprint produced by export_recipe under target_parent."""
    parent = _op(target_parent) if target_parent else _current_parent()
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
        node = parent.create(normalize_op_type(op_type), name)
        created[name] = node
        try:
            node.nodeX, node.nodeY = spec.get("position", [0, 0])
        except Exception:  # noqa: BLE001
            pass
        for k, v in spec.get("params", {}).items():
            if node.par[k] is not None:
                try:
                    node.par[k].val = v
                except Exception:  # noqa: BLE001
                    pass
        # Restore expressions after constants, so an expression always wins
        # over a stale captured value for the same parameter.
        for k, expr in (spec.get("expressions") or {}).items():
            if node.par[k] is not None:
                try:
                    par = node.par[k]
                    par.expr = expr
                    par.mode = getattr(type(par.mode), "EXPRESSION", par.mode)
                except Exception:  # noqa: BLE001
                    pass
        # Authored DAT text (GLSL, callbacks, tables). Omitted from the recipe
        # only when a file already holds it; when present it is the only copy.
        text = spec.get("text")
        if text is not None and hasattr(node, "text"):
            try:
                node.text = text
            except Exception:  # noqa: BLE001
                pass
    # 2) wire inputs using the original source node names (now under target_parent).
    wired = 0
    unresolved = []
    for spec in nodes:
        dst = created.get(spec.get("name"))
        if dst is None:
            continue
        for idx, src_name in spec.get("inputs", {}).items():
            # Older recipes recorded a full path here while this lookup has
            # always been by name, so every wire silently failed to reconnect.
            # Accept both, and say so when a source cannot be found at all.
            src = created.get(src_name) or created.get(
                str(src_name).rsplit("/", 1)[-1])
            if src is None:
                unresolved.append(f"{spec.get('name')}[{idx}] <- {src_name}")
                continue
            try:
                dst.inputConnectors[int(idx)].connect(src.outputConnectors[0])
                wired += 1
            except Exception as e:  # noqa: BLE001
                unresolved.append(f"{spec.get('name')}[{idx}] <- {src_name}: {e}")
    out = {"ok": True, "created": len(created), "wired": wired,
           "paths": [n.path for n in created.values()]}
    if unresolved:
        out["unwired"] = unresolved
    return out


def _do_project_info():
    """Report the TouchDesigner build, bridge version, FPS, and project name."""
    # app.version still reports the legacy generation ("099"); the public
    # year.build identifier lives on app.build (dcc-mcp #5). Version-compat
    # checks compare against "2023.11000"-style builds, so they need the
    # latter -- td_build was previously unusable for that.
    return {"ok": True,
            "td_build": touchdesigner_release(getattr(app, "version", None),
                                              getattr(app, "build", None)),
            "td_version": str(getattr(app, "version", "")),
            "bridge_version": BRIDGE_VERSION,
            "server_started_at": _STARTED_AT,
            "fps": project.cookRate,
            "file": project.name}



# --- Any-family capture through a transient OP Viewer TOP ------------------
# A CHOP/SOP/POP/DAT/COMP has no pixels of its own; its network-editor viewer
# does. Verified 2025.33230: an OP Viewer TOP aimed with a RELATIVE path and
# outputresolution='custom' renders within ~40 frames and is all-zero before
# that -- so the capture cannot be same-frame. It runs on the HTTP thread
# (see _invoke), polling the main thread between frames, and the viewer is
# always destroyed. Undo is suspended so no helper operator lands in the
# user's Ctrl+Z history. Technique after Embody `capture_op` (v6.2.19).
VIEWER_PREFIX = "td_mcp_viewer_"
VIEWER_WAIT_SECONDS = float(os.environ.get("TD_MCP_VIEWER_WAIT", "4"))


class _undo_off:
    def __enter__(self):
        self._prev = None
        try:
            self._prev = ui.undo.state  # noqa: F821 - TD global
            ui.undo.state = False  # noqa: F821
        except Exception:  # noqa: BLE001
            self._prev = None
        return self

    def __exit__(self, *exc):
        if self._prev is not None:
            try:
                ui.undo.state = self._prev  # noqa: F821
            except Exception:  # noqa: BLE001
                pass
        return False


def _viewer_host():
    home = _bridge_home()
    host = op(home) if home and home != "/" else None  # noqa: F821
    return host if host is not None else op("/project1")  # noqa: F821


def _viewer_prepare(path, width=960):
    """Main thread: ``{'is_top': True}``, ``{'viewer': path, ...}`` or an error."""
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found: %s" % path}
    if _family_of(node) == "TOP":
        return {"ok": True, "is_top": True}
    host = _viewer_host()
    if host is None:
        return {"ok": False, "error": "no COMP to host a viewer in"}
    with _undo_off():
        for child in list(getattr(host, "children", [])):
            if str(child.name).startswith(VIEWER_PREFIX):
                try:
                    child.destroy()  # a capture interrupted mid-wait
                except Exception:  # noqa: BLE001
                    pass
        viewer = host.create("opviewerTOP", VIEWER_PREFIX + secrets.token_hex(3))
        try:
            viewer.nodeX, viewer.nodeY = -4000, -4000
            try:
                viewer.par.opviewer = viewer.relativePath(node)
            except Exception:  # noqa: BLE001 - older builds: absolute path
                viewer.par.opviewer = node.path
            w = max(320, int(width or 960))
            viewer.par.outputresolution = "custom"
            viewer.par.resolutionw = w
            viewer.par.resolutionh = int(round(w * 9 / 16))
            try:
                viewer.par.outputaspect = "resolution"
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            viewer.destroy()
            return {"ok": False, "error": "could not aim a viewer at %s: %s" % (node.path, exc)}
    return {"ok": True, "viewer": viewer.path, "family": _family_of(node),
            "op_type": _op_type(node)}


def _viewer_has_content(viewer_path):
    try:
        arr = op(viewer_path).numpyArray()  # noqa: F821
        return arr is not None and arr.size > 0 and float(arr.max()) > 0.0
    except Exception:  # noqa: BLE001
        return False


def _viewer_destroy(viewer_path):
    with _undo_off():
        try:
            node = op(viewer_path)  # noqa: F821
            if node is not None:
                node.destroy()
        except Exception:  # noqa: BLE001
            pass
    return True


def _capture_any_family(args):
    """HTTP thread: capture a TOP directly, anything else through a viewer."""
    path = args.get("path")
    prep = _call_on_main(_viewer_prepare, path, args.get("width", 960))
    if not prep.get("ok"):
        return _wrap(prep)
    if prep.get("is_top"):
        return _call_on_main(_run_tool, "capture_viewport", args)
    viewer = prep["viewer"]
    waited, rendered = 0.0, False
    try:
        start = time.time()
        while time.time() - start < VIEWER_WAIT_SECONDS:
            time.sleep(0.05)
            if _call_on_main(_viewer_has_content, viewer):
                rendered = True
                break
        waited = round(time.time() - start, 2)
        res = _call_on_main(_run_tool, "capture_viewport", dict(args, path=viewer))
    finally:
        _call_on_main(_viewer_destroy, viewer)
    if isinstance(res, dict):
        res = dict(res)
        res.update({"source": path, "captured_via": "opviewerTOP",
                    "family": prep.get("family"), "op_type": prep.get("op_type"),
                    "viewer_wait_s": waited})
        if not rendered:
            res["warning"] = ("the viewer had not rendered after %.1fs; the image "
                              "is probably blank (is the timeline playing?)" % waited)
        else:
            res["note"] = ("viewer capture: is_black/is_flat judge the editor's "
                           "viewer (a CHOP graph or SOP viewport), not a render")
    return res


#: Tools that must span frames. They run on the HTTP thread and marshal each
#: step to the main thread themselves; called ON the main thread (a batch, the
#: Textport) they fall back to the single-frame handler.
_MULTI_FRAME_TOOLS = {"capture_viewport": _capture_any_family,
                      "observe": lambda a: _observe_across_frames(a),
                      "get_performance": lambda a: _perf_across_frames(a)}


def _do_capture_viewport(path, detail="normal"):
    """Save a TOP/viewer to a PNG and compute a quality verdict."""
    import tempfile
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    family = _family_of(node)
    if family and family != "TOP":
        # Reached on the main thread (inside `batch`, the in-TD agent, the
        # Textport), where frames cannot pass: the OP Viewer route needs them.
        # Saving the operator itself would write a .bchan/.tog, not a PNG.
        return {"ok": False, "error_code": "needs_frames",
                "error": "%s is a %s: capturing it needs a few frames, so call "
                         "capture_viewport directly, not inside batch" % (node.path, family)}
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
        flat = _pixels_0_255(np, arr.reshape(-1, arr.shape[-1])[:, :3])
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


_MOTION_GRID = 8


def _pixels_0_255(np, arr):
    """Pixel data on a 0..255 scale, whatever TouchDesigner handed back.

    ``TOP.numpyArray()`` returns floats in 0..1 for float TOPs -- and for the
    default 8-bit Noise TOP in the first live session, which came back with
    mean 0.46 and was judged ``is_black`` because the thresholds assumed
    0..255. Every healthy render was scoring -20 for "black". Integer arrays
    are already on that scale; float arrays whose maximum does not exceed 1
    are rescaled.
    """
    data = arr.astype(float)
    if np.issubdtype(arr.dtype, np.floating) and data.size and float(data.max()) <= 1.0:
        data = data * 255.0
    return data
#: Frames sampled by `observe` by default. Each costs one forced cook, so this
#: stays small: enough to tell motion from stillness, not a recording.
_MOTION_FRAMES = 3
_MOTION_MAX_FRAMES = 12


def _frame_signature(node):
    """An 8x8 mean-luminance fingerprint of the node's current output.

    Comparing whole frames is expensive and comparing scalar means is useless:
    a pattern that scrolls across the canvas holds its mean exactly constant
    while every pixel changes. A coarse spatial grid catches that cheaply.
    """
    try:
        import numpy as np
        arr = node.numpyArray()
    except Exception:  # noqa: BLE001
        return []
    try:
        rgb = _pixels_0_255(np, arr[..., :3])
        luma = (0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1]
                + 0.0722 * rgb[..., 2])
        h, w = luma.shape[0], luma.shape[1]
        if h < _MOTION_GRID or w < _MOTION_GRID:
            return [float(luma.mean())]
        ys = np.linspace(0, h, _MOTION_GRID + 1).astype(int)
        xs = np.linspace(0, w, _MOTION_GRID + 1).astype(int)
        return [round(float(luma[ys[r]:ys[r + 1], xs[c]:xs[c + 1]].mean()), 3)
                for r in range(_MOTION_GRID) for c in range(_MOTION_GRID)]
    except Exception:  # noqa: BLE001
        return []


def _do_observe(path, frames=_MOTION_FRAMES, detail="normal"):
    """Capture a still *and* judge whether the render is moving.

    `capture_viewport` answers "is there a picture"; this answers "is anything
    happening". A feedback or audio-reactive network that renders a correct
    single frame and never changes is structurally valid, visually plausible
    and completely broken -- and no single-frame verdict can see it.

    Each frame costs one forced cook, so the frame count is capped: this is a
    motion check, not a recording.
    """
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    # Reached on the main thread (inside `batch`, the in-TD agent). All samples
    # here land in ONE frame: absTime does not advance and feedback does not
    # step, so a working animation reads as frozen (verified 2025.33230: max
    # tile delta 0.000 on a live feedback network). Say so instead of guessing;
    # `observe` called directly samples across real frames (_observe_across_frames).
    still = _do_capture_viewport(path, detail)
    verdict = dict(still.get("verdict") or {})
    verdict.update({"is_static": None,
                    "motion_caption": "motion needs frames to pass: call observe "
                                      "directly, not inside batch"})
    return {"ok": True, "path": node.path, "verdict": verdict,
            "motion": {"is_static": None, "frames": 0}, "file": still.get("file"),
            "note": "same-frame call: motion not judged"}


def perf_summary(snapshots, fps=60.0, limit=25):
    """Median per-operator cost over several per-frame snapshots. Pure.

    ``snapshots`` is a list of ``{path: (type, cpu_ms, gpu_ms)}``. A single
    reading is the LAST cook, and right after a build that includes one-off
    costs (first GPU allocation): verified 2025.33230, one snapshot of a fresh
    9-operator network read 167% of a frame while its steady state is ~2.4%.
    """
    frame_ms = 1000.0 / (fps or 60.0)
    per = {}
    for snap in snapshots:
        for path, (op_type, cpu, gpu) in snap.items():
            per.setdefault(path, [op_type, [], []])
            per[path][1].append(float(cpu or 0))
            per[path][2].append(float(gpu or 0))

    def median(xs):
        xs = sorted(xs)
        n = len(xs)
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0

    rows = []
    for path, (op_type, cpus, gpus) in per.items():
        cpu, gpu = median(cpus), median(gpus)
        peak = max(c + g for c, g in zip(cpus, gpus))
        rows.append({"path": path, "type": op_type, "cpu_ms": round(cpu, 4),
                     "gpu_ms": round(gpu, 4), "peak_ms": round(peak, 4),
                     "budget_pct": round(100.0 * (cpu + gpu) / frame_ms, 2)})
    rows.sort(key=lambda r: -(r["cpu_ms"] + r["gpu_ms"]))
    return {"ok": True, "fps": fps, "frame_budget_ms": round(frame_ms, 3), "units": "ms",
            "samples": len(snapshots), "statistic": "median per frame (peak = worst frame)",
            "count": len(rows),
            "live_total_budget_pct": round(sum(r["budget_pct"] for r in rows), 2),
            "hotspots": rows[:5], "cooks": rows[:int(limit or 25)]}


def _perf_sample(path, depth=1):
    """Main thread: cook ``path``'s network once (its leaves pull everything
    upstream) and read each operator's cook times for that frame."""
    start = _op(path) if path else root  # noqa: F821
    if start is None:
        return None
    if getattr(start, "isCOMP", False):
        try:
            nodes = start.findChildren(maxDepth=max(1, min(int(depth or 1), MAX_DEPTH)))
        except Exception:  # noqa: BLE001
            nodes = list(getattr(start, "children", []))
    else:
        nodes = [start]
    for node in nodes:
        if not _output_targets(node):
            _force_cook(node)
    return {n.path: (_op_type(n), getattr(n, "cpuCookTime", 0) or 0,
                     getattr(n, "gpuCookTime", 0) or 0) for n in nodes}


def _perf_across_frames(args):
    """HTTP thread: ``get_performance`` with ``samples`` > 1 -- one cook per real
    frame, median per operator. ``samples`` 1 (default) is the classic
    single-frame read with stale marking."""
    samples = max(1, min(int(args.get("samples") or 1), 60))
    if samples == 1:
        return _call_on_main(_run_tool, "get_performance", args)
    path, depth = args.get("path"), args.get("depth", 1)
    snapshots = []
    for _ in range(samples):
        snap = _call_on_main(_perf_sample, path, depth)
        if snap is None:
            return _wrap({"ok": False, "error": "not found: %s" % path})
        snapshots.append(snap)
        time.sleep(0.05)  # > one frame at 60 fps
    try:
        fps = float(_call_on_main(lambda: project.cookRate))  # noqa: F821
    except Exception:  # noqa: BLE001
        fps = 60.0
    out = perf_summary(snapshots, fps, args.get("limit", 25))
    out["note"] = ("each sample forced one cook of the network, so this is the "
                   "cost of cooking it every frame -- an upper bound for parts "
                   "that would not recook on their own")
    return out


def _observe_sample(path):
    """Main thread: one forced cook and an 8x8 luma signature of ``path``."""
    node = _op(path)
    if node is None:
        return None
    _force_cook(node)
    return _frame_signature(node)


def _observe_across_frames(args):
    """HTTP thread: sample ``frames`` signatures with a real frame between each.

    ``observe`` exists to catch a network that renders one good frame and then
    freezes, so its samples must straddle frames -- see _do_observe.
    """
    path = args.get("path")
    detail = args.get("detailLevel", "normal")
    count = max(2, _cap(args.get("frames", _MOTION_FRAMES), _MOTION_MAX_FRAMES))
    first = _call_on_main(_observe_sample, path)
    if first is None:
        return _wrap({"ok": False, "error": "not found: %s" % path})
    signatures = [first] if first else []
    for _ in range(count - 1):
        time.sleep(0.05)  # > one frame at 60 fps: let the timeline advance
        sig = _call_on_main(_observe_sample, path)
        if sig:
            signatures.append(sig)
    motion = motion_verdict(signatures)
    still = _call_on_main(_do_capture_viewport, path, detail)
    verdict = dict(still.get("verdict") or {})
    verdict.update({"is_static": motion["is_static"], "motion_caption": motion["caption"]})
    out = {"ok": True, "path": path, "verdict": verdict, "motion": motion,
           "is_static": motion["is_static"]}
    if still.get("file"):
        out["file"] = still["file"]
    if not signatures:
        out["note"] = ("no pixel data available on this operator; motion "
                       "could not be judged")
    return out


def _do_map_network(path, depth=2):
    """Emit a Graphviz DOT graph of the network (nested github-mcp `map` tool).

    Shows node connections, positions and non-default param counts so an
    agent can reason about topology spatially. Rendered by `dot -Tpng/-Tsvg`."""
    start_node = _op(path) or root
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
                f'pos="{getattr(node, "nodeX", 0)},{getattr(node, "nodeY", 0)}!"];')
        nodes.append(ndef)
        for _o, dst, _i in _output_targets(node):
            edges.append(f'  "{node.path}" -> "{dst.path}";')
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
    # td://node/project1/x arrives without its leading slash; a relative path
    # would resolve against the bridge DAT and find nothing.
    if rest and not rest.startswith(("/", "*")):
        rest = "/" + rest
    if kind == "project":
        return _do_project_info()
    if kind == "errors":
        return _do_get_errors(rest)
    if kind == "chop":
        node = _op(rest)
        if node is None:
            return {"ok": False, "error": "not found"}
        return {"ok": True, "channels": [c.name for c in node.chans()]}
    if kind == "node":
        node = _op(rest)
        if node is None:
            return {"ok": False, "error": "not found"}
        return {"ok": True, "name": node.name, "type": node.type,
                "op_type": _op_type(node), "path": node.path,
                "children": len(node.children)}
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
        refused = _guard_call(tool, call.get("args", {}))
        if refused is not None:
            results.append({"tool": tool, "result": _wrap(refused)})
            continue
        try:
            with _undo_block(tool):
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
def _do_read_chop(path, channel=None, samples=10, format="stats"):
    """Read a CHOP. Reduces per channel by default; raw values on request.

    ``format='stats'`` (default) returns min/max/mean/std per channel -- the
    answer to "what did this chain do to my data" in four numbers instead of
    `samples` of them. ``format='values'`` restores the old raw slice for the
    cases that genuinely need sample-level detail.
    """
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    try:
        # CHOP_Class: chans() / chan() are methods, not attributes. Reading
        # `node.chans` gives a bound method, and iterating that is the
        # "'builtin_function_or_method' object is not iterable" the first live
        # session returned. chan() answers None for a name it does not have.
        chans = list(node.chans()) if channel is None else [node.chan(channel)]
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"unknown channel {channel!r}: {e}"}
    if channel is not None and (not chans or chans[0] is None):
        return {"ok": False, "error": f"unknown channel {channel!r}"}
    num_samples = getattr(node, "numSamples", None)
    if format == "values":
        samples = _cap(samples, MAX_SAMPLES)
        out = {}
        for c in chans:
            out[c.name] = [round(float(v), 4) for v in c.vals[:samples]]
        return {"ok": True, "format": "values", "numSamples": num_samples,
                "channels": out}
    stats = {c.name: summarize_series(c.vals) for c in chans}
    return {"ok": True, "format": "stats", "numSamples": num_samples,
            "numChannels": len(chans), "channels": stats}


def _do_diff_chop(path, other_path, channel=None):
    """Compare two CHOPs channel by channel.

    The read an agent actually wants after inserting a filter: not "what are
    the values" but "what changed". Channels present on only one side are
    reported rather than silently dropped.
    """
    a = _do_read_chop(path, channel, format="stats")
    if not a.get("ok"):
        return a
    b = _do_read_chop(other_path, channel, format="stats")
    if not b.get("ok"):
        return b
    ca, cb = a["channels"], b["channels"]
    deltas = {}
    for name in sorted(set(ca) & set(cb)):
        deltas[name] = {
            k: (None if ca[name][k] is None or cb[name][k] is None
                else round(cb[name][k] - ca[name][k], 4))
            for k in ("min", "max", "mean", "std")}
    return {"ok": True, "from": path, "to": other_path, "deltas": deltas,
            "only_in_from": sorted(set(ca) - set(cb)),
            "only_in_to": sorted(set(cb) - set(ca))}


def _pop_attributes(node):
    """POP attribute metadata, by class.

    The API is ``pointAttributes`` / ``primAttributes`` / ``vertAttributes``:
    **properties**, each an iterable ``Attributes`` collection whose members
    carry ``name`` / ``size`` / ``type`` / ``vals``. There is no
    ``attributes()`` method -- the bridge called one, so `read_pop` reported
    `attributes: {}` for a sphere with 252 points and two attributes.
    """
    out = {}
    for cls, holder in (("point", "pointAttributes"),
                        ("prim", "primAttributes"),
                        ("vert", "vertAttributes")):
        collection = getattr(node, holder, None)
        if collection is None:
            continue
        entries = {}
        try:
            for attr in collection:
                entries[str(attr.name)] = {
                    "size": _int_of(attr, "size", 1),
                    "type": str(getattr(attr, "type", "")),
                    "isArray": bool(getattr(attr, "isArray", False)),
                    "arraySize": _int_of(attr, "arraySize", 0),
                }
        except Exception:  # noqa: BLE001
            continue
        if entries:
            out[cls] = entries
    return out


def _pop_values(node, name):
    """Values for one POP attribute. ``Attribute.vals`` first, ``points()``
    as the fallback for older builds."""
    for cls in ("pointAttributes", "primAttributes", "vertAttributes"):
        collection = getattr(node, cls, None)
        if collection is None:
            continue
        try:
            for attr in collection:
                if str(attr.name) == name:
                    vals = getattr(attr, "vals", None)
                    if vals is not None:
                        return list(vals)
        except Exception:  # noqa: BLE001
            pass
    return list(node.points(name))


def _do_read_pop(path, attribute=None, format="stats", max_points=None):
    """Read a POP: attribute metadata always, values only when affordable.

    ``POP.points(attr, start, count)`` accepts both slicing arguments and
    **ignores them** -- TouchDesigner returns the whole attribute either way,
    and Embody measured a 4,096-point "slice" off a 160k POP as *slower*
    (80ms) than reading all of it. So there is no cheap partial read to fall
    back on: either the point count is small enough to read whole, or the
    values are not read at all. Metadata costs ~0.02ms at any point count.
    """
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    # `numPoints` is a property and `numPrims` a bound method on the same
    # object -- TD is not consistent here, so read both tolerantly.
    num_points = _int_of(node, "numPoints")
    attrs = _pop_attributes(node)
    out = {"ok": True, "path": node.path, "numPoints": num_points,
           "numPrims": _int_of(node, "numPrims"),
           "attributes": attrs}
    if not attrs:
        out["note"] = ("no point/prim/vertex attributes -- is this a POP? "
                       "(TOPs, CHOPs and SOPs have none)")
    if format == "meta":
        return out

    ceiling = _cap(max_points if max_points is not None else MAX_SAMPLES,
                   MAX_SAMPLES)
    if num_points > ceiling:
        out["values_skipped"] = (
            f"{num_points} points exceeds the {ceiling}-point readback ceiling; "
            f"POP.points() cannot slice, so a partial read is not available. "
            f"Raise max_points deliberately if the stall is acceptable.")
        return out

    names = ([attribute] if attribute
             else [n for cls in attrs.values() for n in cls])
    values = {}
    for name in names:
        try:
            raw = _pop_values(node, name)
        except Exception as e:  # noqa: BLE001
            values[name] = {"error": str(e)}
            continue
        if format == "values":
            values[name] = [_json_safe(v) for v in raw]
        else:
            # A vector attribute (P, N, Cd) summarises per component.
            if raw and isinstance(raw[0], (list, tuple)):
                width = len(raw[0])
                values[name] = [summarize_series([r[i] for r in raw])
                                for i in range(width)]
            else:
                values[name] = summarize_series(raw)
    out["format"] = format
    out["values"] = values
    return out


def _do_read_top(path, detail="brief"):
    """Read a TOP's pixel dimensions (and aspect ratio when detail != 'brief')."""
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


def _do_read_dat(path, rows=10, format="stats"):
    """Read a DAT table. Reduces by default; full rows on request.

    ``format='stats'`` returns per-column stats plus the first and last few
    rows -- measured ~17x smaller than a full dump, and it preserves the two
    things a caller reads a table for: the header and the shape of the data.
    ``format='rows'`` restores the old behaviour (first `rows` rows verbatim).
    """
    node = _op(path)
    if node is None:
        return {"ok": False, "error": "not found"}
    num_rows = int(getattr(node, "numRows", 0) or 0)
    num_cols = int(getattr(node, "numCols", 0) or 0)
    if format == "rows":
        rows = _cap(rows, MAX_ROWS)
        data = [[node[r, c].val for c in range(num_cols)]
                for r in range(min(rows, num_rows))]
        return {"ok": True, "format": "rows", "numRows": num_rows,
                "numCols": num_cols, "rows": data}

    capped = _cap(num_rows, MAX_ROWS)
    table = [[node[r, c].val for c in range(num_cols)] for r in range(capped)]
    # A table whose first row is non-numeric while the rest are numeric is a
    # header; summarising it into the column stats would poison every one.
    header, body = None, table
    if table and num_cols and any(
            summarize_series([table[0][c]])["count"] == 0
            for c in range(num_cols)):
        header, body = table[0], table[1:]
    columns = {}
    for c in range(num_cols):
        name = str(header[c]) if header else f"col{c}"
        columns[name] = summarize_column([row[c] for row in body])
    edges = edge_rows(table)
    return {"ok": True, "format": "stats", "numRows": num_rows,
            "numCols": num_cols, "header": header, "columns": columns,
            "rows": edges["rows"], "elided": edges["elided"]}


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
        # Hash what TouchDesigner STORES, not what was sent. A table DAT
        # re-normalises its text (tab-delimited columns, newline-delimited
        # rows), so a sha of the input never matched on the next CAS write.
        stored = getattr(node, "text", text)
        stored_bytes = stored.encode("utf-8") if isinstance(stored, str) else stored
        new_sha = hashlib.sha256(stored_bytes).hexdigest()
        out = {"ok": True, "path": node.path, "sha256": new_sha,
               "normalized": stored_bytes != encoded}
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
    "get_parameters": lambda a: _do_get_parameters(a.get("path"), a.get("detailLevel", "normal"),
                                                   a.get("names"), a.get("non_default", False)),
    "pulse_parameter": lambda a: _do_pulse_parameter(a.get("path"), a.get("param")),
    "describe_op_type": lambda a: _do_describe_op_type(a.get("op_type") or a.get("type"),
                                                       a.get("pattern"), a.get("page"),
                                                       a.get("include_menus", True)),
    "get_parameter_info": lambda a: _do_get_parameter_info(a.get("path"), a.get("names")),
    "bind_parameter_expression": lambda a: _do_bind_parameter_expression(a.get("path"), a.get("param"), a.get("expression")),
    "get_errors": lambda a: _do_get_errors(a.get("path"), a.get("include_warnings", True),
                                           a.get("include_shader", True), a.get("recurse", False)),
    "clear_script_errors": lambda a: _do_clear_script_errors(a.get("path"), a.get("recurse", False)),
    "execute_python": lambda a: _do_execute_python(a.get("code")),
    "list_nodes": lambda a: _do_list_nodes(a.get("path"), a.get("detailLevel", "normal")),
    "project_info": lambda a: _do_project_info(),
    "capture_viewport": lambda a: _do_capture_viewport(a.get("path"), a.get("detailLevel", "normal")),
    "observe": lambda a: _do_observe(a.get("path"), a.get("frames", 3),
                                     a.get("detailLevel", "normal")),
    "get_resource": lambda a: _do_get_resource(a.get("uri")),
    "describe_td_tools": lambda a: _do_describe(),
    "batch": lambda a: _do_batch(a.get("ops", [])),
    "read_chop": lambda a: _do_read_chop(a.get("path"), a.get("channel"), a.get("samples", 10),
                                         a.get("format", "stats")),
    "diff_chop": lambda a: _do_diff_chop(a.get("path"), a.get("other_path"), a.get("channel")),
    "read_pop": lambda a: _do_read_pop(a.get("path"), a.get("attribute"),
                                       a.get("format", "stats"), a.get("max_points")),
    "read_top": lambda a: _do_read_top(a.get("path"), a.get("detailLevel", "brief")),
    "read_dat": lambda a: _do_read_dat(a.get("path"), a.get("rows", 10), a.get("format", "stats")),
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
    "get_performance": lambda a: _do_get_performance(a.get("path"), a.get("max_age_frames", 10),
                                                     a.get("depth", 1), a.get("limit", 25)),
    "validate_network": lambda a: _do_validate_network(a.get("path"), a.get("depth", 2)),
    "set_flags": lambda a: _do_set_flags(a.get("path"), a.get("flags")),
    "find_nodes": lambda a: _do_find_nodes(a.get("path"), a.get("query"), a.get("type"), _cap(a.get("depth", 4), MAX_DEPTH)),
    "set_node_position": lambda a: _do_set_node_position(a.get("path"), a.get("x", 0), a.get("y", 0)),
    "timeline": lambda a: _do_timeline(a.get("action"), a.get("value"), a.get("force", False)),
    "export_recipe": lambda a: _do_export_recipe(a.get("path"), _cap(a.get("depth", 3), MAX_DEPTH)),
    "import_recipe": lambda a: _do_import_recipe(a.get("recipe"), a.get("target_parent", "*here")),
    "build_network": lambda a: _do_build_network(a.get("spec"), a.get("target_parent"),
                                                 a.get("replace", False)),
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


class _Handler(BaseHTTPRequestHandler):
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

    def _read_body(self):
        """Consume the request body -- always, before any response.

        Answering a request (a 401, a 403) and closing the connection while
        its body is still unread makes Windows send a RST, and the client
        sees "[WinError 10053] An established connection was aborted" instead
        of the status code. Bounded, because this is loopback but not
        unlimited.
        """
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        length = max(0, min(length, MAX_BODY_BYTES))
        return self.rfile.read(length) if length else b"{}"

    def do_POST(self):
        raw = self._read_body()

        # DNS rebind protection
        if not self._check_dns_rebind():
            return

        # Legacy /mcp endpoint (backward compatible)
        if self.path == "/mcp":
            if not self._auth_ok():
                self._send({"ok": False, "error": "unauthorized"}, 401)
                return
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
                result = _invoke(tool, req.get("args", {}))
                ok = result.get("ok") if isinstance(result, dict) else True
                _ws_push({"tool": tool, "ok": ok})
                self._send(result or {"ok": True})
            except Exception as e:
                _ws_push({"tool": tool, "ok": False, "error": str(e)})
                self._send(_wrap({"ok": False, "error": str(e)}), 500)
            return

        # Streamable HTTP JSON-RPC endpoint (root path)
        if self.path in ("/", "/stream", "/rpc"):
            self._handle_jsonrpc_post(raw)
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
            # Answer with the client's protocol version when we speak it,
            # rather than asserting one version at every client forever.
            version, exact = negotiate_protocol_version(
                params.get("protocolVersion"))
            result = {
                "protocolVersion": version,
                "capabilities": server_capabilities(version),
                "serverInfo": {"name": "td-mcp-bridge",
                               "version": BRIDGE_VERSION},
            }
            if not exact:
                result["instructions"] = (
                    "td-mcp bridge speaks "
                    + ", ".join(SUPPORTED_PROTOCOL_VERSIONS)
                    + "; answering with " + version + ".")
            return self._jsonrpc_result(req_id, result)
        if method == "ping":
            return self._jsonrpc_result(req_id, {})
        if method == "tools/list":
            tools = [
                {
                    "name": name,
                    "description": _tool_description(name),
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
            if name not in _DISPATCH:
                return self._jsonrpc_error(req_id, -32601, f"unknown tool: {name}")
            try:
                result = _invoke(name, arguments)
            except Exception as e:  # noqa: BLE001 - surfaces main-thread timeouts too
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

    def _handle_jsonrpc_post(self, raw):
        # Session management
        self._current_session_id, session = self._get_or_create_session()
        if not self._auth_ok_for_session(session):
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"jsonrpc": "2.0", "error": {"code": -32600, "message": "Unauthorized"}}).encode())
            return

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
                with open(_chat_ui_path(), "rb") as f:
                    self._send_html(f.read())
            except FileNotFoundError:
                self._send({"ok": False, "error": "chat_ui.html not found"})
            return
        if self.path == "/api/status":
            self._send({"ok": True, "status": "running", "port": TD_MCP_PORT,
                        "auth": True, "allow_exec": ALLOW_EXEC,
                        "bridge_version": BRIDGE_VERSION, "started_at": _STARTED_AT})
        elif self.path.startswith("/api/resource?"):
            m = re.search(r"uri=([^&]+)", self.path)
            uri = _urldecode(m.group(1)) if m else ""
            self._send(_call_on_main(lambda: _wrap(_do_get_resource(uri))))
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
                        result = _invoke(tool, req.get("args", {}))
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
    return unquote(s)  # module-level import (line 83)


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
    global _server, _thread, _STARTED_AT
    if _server is not None:
        debug("td_mcp already running")
        return
    # A previous load of this DAT may still hold the port (link-mode reload).
    for srv in _orphaned_servers(TD_MCP_PORT):
        debug("stopping a bridge left running by a previous load of this DAT")
        _shutdown_server(srv)
    global _MAIN_THREAD
    # start() is called from the Execute DAT or the Textport: this IS the
    # main thread. pump() re-asserts it on every frame.
    _MAIN_THREAD = threading.current_thread()
    try:
        host = f"TouchDesigner {app.build}"  # noqa: F821 - TD global
    except Exception:  # noqa: BLE001
        host = "an unknown host"
    print(f"[td_mcp] bridge v{BRIDGE_VERSION} on {host}")
    print(f"[td_mcp] auth token: {AUTH_TOKEN}")
    if PROTECTED:
        print(f"[td_mcp] protected paths: {PROTECTED}")
    if not ALLOW_EXEC:
        print("[td_mcp] execute_python DISABLED (TD_MCP_ALLOW_EXEC=0)")
    _server = HTTPServer(("127.0.0.1", TD_MCP_PORT), _Handler)
    _Handler.AUTH_TOKEN = AUTH_TOKEN  # set auth token on handler class
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()
    _STARTED_AT = time.time()
    debug(f"td_mcp bridge v{BRIDGE_VERSION} started on http://127.0.0.1:{_server.server_address[1]}")


def stop():
    global _server, _thread
    # `_server` is None after a link-mode reload even though a server from
    # the previous module load is still up; adopt it so Restart works.
    targets = [_server] if _server is not None else _orphaned_servers(TD_MCP_PORT)
    if not targets:
        return
    for srv in targets:
        _shutdown_server(srv)
    _server = None
    _thread = None
    debug("td_mcp bridge stopped")


def status():
    pump_age = (time.time() - _last_pump) if _last_pump else None
    return {"running": _server is not None, "port": TD_MCP_PORT,
            "version": BRIDGE_VERSION,
            "auth_token": AUTH_TOKEN, "allow_exec": ALLOW_EXEC,
            "exec_mode": EXEC_MODE,
            "pump_alive": pump_age is not None and pump_age < PUMP_STALL_SECONDS,
            "timeline_playing": _last_pump_playing,
            "pump_age_seconds": None if pump_age is None else round(pump_age, 3),
            "queued": _JOBS.qsize()}
