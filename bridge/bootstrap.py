#!/usr/bin/env python3
"""Build the TD_MCP bridge component inside TouchDesigner.

Run this **once, inside TouchDesigner** and it creates a `/td_mcp` Base COMP
wired to run the bridge. Optionally it saves that COMP as `td_mcp.tox`, which
is then the drag-and-drop artifact everyone else uses.

Three ways to run it, all supported:

    # A. Textport (Alt+T) -- name the repo, then exec:
    TD_MCP_REPO = r'C:/path/to/TD_MCP'
    exec(open(TD_MCP_REPO + '/bridge/bootstrap.py').read())

    # B. Drag bootstrap.py into the network (TD makes a file-backed Text DAT),
    #    then right-click the DAT -> Run Script. The path is discovered from
    #    the DAT's own File parameter; nothing to configure.

    # C. Paste the contents into a Text DAT -> set TD_MCP_REPO in the
    #    CONFIGURATION block below -> Run Script.

    # To also produce the .tox, set this first (any of the above):
    TD_MCP_SAVE_TOX = 1

Re-running is safe: it updates the existing component in place.

**Do not rely on ``__file__``.** Neither `exec(open(...).read())` nor a Text
DAT gives this script a usable `__file__`: the first inherits the Textport's
globals (where it is usually undefined), and the second sets it to the DAT's
*operator path*, so `os.path.abspath('/project1/bootstrap')` becomes
`C:\\project1\\bootstrap` and every lookup beside it fails. `locate_bridge()`
handles this; see its docstring for the search order.

Why a builder and not a checked-in `.tox`
-----------------------------------------
A `.tox` is opaque compressed binary. It cannot be authored, diffed, reviewed
or built without a running TouchDesigner, so a repository that ships only a
`.tox` ships something nobody can inspect and no CI can regenerate. Every
project in the reference library that ships one built it by hand, and
mintiasaikoh's README says so outright: their `mcp_bridge.tox` *is* the saved
output of their `setup_mcp.py`.

So the script is the source of truth and the `.tox` is a build artifact. You
can commit the `.tox` for drag-and-drop convenience; you regenerate it from
here whenever the bridge changes.

What this replaced
------------------
Until v1.12.0 this file contained a **second, forked copy of the entire
bridge** — 1,000+ lines pasted in and then left behind. It had drifted badly:
39 tools against the bridge's 46, `COMP.create` called with its arguments
reversed at three sites, and all four of the `node.errors` reads that Wave 12
fixed. Anyone who ran it installed a broken bridge, and
`scripts/check_bridge_stale.py` never noticed because it only guards
`td_mcp_bridge.py`. There is now exactly one copy of the bridge logic, and
this script only ever *references* it.

Two ways the component can carry the bridge
-------------------------------------------
``link``   The Text DAT's `file` parameter points at `td_mcp_bridge.py` with
           **Sync to File** on, so TouchDesigner reloads it whenever the file
           changes on disk. The `.py` stays the single source of truth: edit,
           save, done. Best while developing, and the mode this defaults to
           when the source file is present.

``embed``  The bridge source is pasted into the Text DAT. The component is
           then self-contained and survives being moved to a machine that has
           never seen this repository — which is what a shipped `.tox` has to
           be. Saving a `.tox` forces this mode.
"""

import os

# ---------------------------------------------------------------------------
# CONFIGURATION
#
# Set these in the Textport before exec()ing this file, or edit them here if
# you pasted the script into a Text DAT (a Textport variable is not visible
# inside a DAT's own module namespace).
# ---------------------------------------------------------------------------
#: Path to the td-mcp checkout, e.g. r'C:/path/to/TD_MCP'. Leave empty to let
#: `locate_bridge()` find it. Accepts the repo root or the `bridge/` folder.
REPO = globals().get("TD_MCP_REPO", os.environ.get("TD_MCP_REPO", ""))
#: Full path to td_mcp_bridge.py, if it does not sit under REPO.
BRIDGE_PATH = globals().get("TD_MCP_BRIDGE_PATH",
                            os.environ.get("TD_MCP_BRIDGE_PATH", ""))

COMP_NAME = globals().get("TD_MCP_COMP_NAME", "td_mcp")
PORT = int(globals().get("TD_MCP_PORT", os.environ.get("TD_MCP_PORT", "9980")))
#: "link" | "embed" | "auto" (link when the source file is readable)
MODE = globals().get("TD_MCP_MODE", "auto")
#: Truthy → also write `<COMP_NAME>.tox` next to the project and force embed.
SAVE_TOX = globals().get("TD_MCP_SAVE_TOX", False)
#: Where to put the component. Default: the project's root container.
TARGET = globals().get("TD_MCP_TARGET", "/project1")

BRIDGE_FILENAME = "td_mcp_bridge.py"

# ---------------------------------------------------------------------------
# Startup / control callbacks written into the component
# ---------------------------------------------------------------------------
# Execute DAT: Start fires when TouchDesigner launches, Create when the
# component is dropped into a network. A dragged-in .tox never sees Start, so
# without Create a drag-and-drop install would look inert until the next
# restart — the single most confusing thing a drop-in component can do.
STARTUP_CODE = '''# td_mcp — component startup. Generated by bridge/bootstrap.py.
#
# Applies the component's custom parameters as environment variables and then
# starts the bridge. The bridge reads its configuration from os.environ at
# import time, and a Text DAT is imported lazily on first `.module` access, so
# the environment must be set BEFORE that first access — not after.


def _apply_env():
    import os
    comp = me.parent()
    os.environ['TD_MCP_PORT'] = str(int(comp.par.Port.eval()))
    token = str(comp.par.Token.eval()).strip()
    if token:
        os.environ['TD_MCP_AUTH_TOKEN'] = token
    os.environ['TD_MCP_ALLOW_EXEC'] = '1' if comp.par.Allowexec.eval() else '0'
    os.environ['TD_MCP_EXEC_MODE'] = 'on' if comp.par.Allowexec.eval() else 'off'
    protected = str(comp.par.Protectedpaths.eval()).strip()
    os.environ['TD_MCP_PROTECTED_PATHS'] = protected


# True between start_bridge() and stop_bridge(). onFrameStart must not touch
# `bridge.module` before start_bridge() has applied the environment: a Text
# DAT imports on first `.module` access and the bridge reads its config at
# import, so an early pump would import it with the defaults and the panel's
# port/token would be displayed but not in effect.
_started = False


def start_bridge():
    global _started
    comp = me.parent()
    _apply_env()
    bridge = comp.op('bridge')
    if bridge is None:
        print('[td_mcp] no `bridge` DAT in', comp.path)
        return
    bridge.module.start()
    _started = True


def stop_bridge():
    global _started
    _started = False
    bridge = me.parent().op('bridge')
    if bridge is not None:
        bridge.module.stop()


def onPlayStateChange(state):
    # Pressing Play after a pause resumes the frame loop; drain immediately so
    # a request that arrived while paused is answered now rather than on the
    # next frame. Pausing gets one final pump, which also records the play
    # state the stall diagnosis reports.
    if _started:
        bridge = me.parent().op('bridge')
        if bridge is not None:
            bridge.module.pump()


def onFrameStart(frame):
    # The bridge's HTTP thread may not touch TouchDesigner objects; it queues
    # each request and waits. This is where the queue is drained -- on the
    # main thread, once per frame, under the bridge's time budget.
    if not _started:
        return
    bridge = me.parent().op('bridge')
    if bridge is not None:
        bridge.module.pump()


def onStart():
    if me.parent().par.Active.eval():
        start_bridge()


def onCreate():
    # Fires when the .tox is dropped into a network.
    if me.parent().par.Active.eval():
        start_bridge()


def onExit():
    stop_bridge()
'''

# Parameter Execute DAT: react to the component's own parameters so the
# component is operable from its parameter panel, not only from the Textport.
PAREXEC_CODE = '''# td_mcp — parameter callbacks. Generated by bridge/bootstrap.py.


def _exec_dat():
    return me.parent().op('startup')


def onValueChange(par, prev):
    ctl = _exec_dat()
    if par.name == 'Active':
        if par.eval():
            ctl.module.start_bridge()
        else:
            ctl.module.stop_bridge()
    elif par.name in ('Port', 'Token', 'Allowexec', 'Protectedpaths'):
        # A live bridge is holding the old port/token. Bounce it so the new
        # value is the one actually in effect, rather than the one displayed.
        if me.parent().par.Active.eval():
            ctl.module.stop_bridge()
            ctl.module.start_bridge()
    return


def onPulse(par):
    ctl = _exec_dat()
    if par.name == 'Restart':
        ctl.module.stop_bridge()
        ctl.module.start_bridge()
    elif par.name == 'Printtoken':
        bridge = me.parent().op('bridge')
        if bridge is not None:
            print('[td_mcp] auth token:', bridge.module.AUTH_TOKEN)
    return
'''


# ---------------------------------------------------------------------------
# Pure helpers (no TouchDesigner needed — exercised by tests/test_bootstrap.py)
# ---------------------------------------------------------------------------
def resolve_mode(mode, source_readable, save_tox):
    """Decide between `link` and `embed`.

    A `.tox` is meant to travel to machines that have never seen this
    repository, so saving one always embeds — a file-linked DAT whose target
    does not exist there loads as an empty DAT, and an empty bridge DAT fails
    as "no module named start" rather than as "the file is missing".

    When the source cannot be read, `auto` resolves to **link**, not embed.
    Embedding is precisely the mode that needs the text, so choosing it here
    guarantees a failure one step later; linking at least works if the file
    exists but this process cannot read it (permissions, a mapped drive).
    Neither mode works with no file at all — `locate_bridge()` refuses first.
    """
    if save_tox:
        return "embed"
    if mode in ("link", "embed"):
        return mode
    return "link" if source_readable else "link"


def bridge_source_path(script_dir, filename=BRIDGE_FILENAME):
    """The bridge `.py` that sits next to this script."""
    return os.path.join(script_dir, filename)


def set_pars(node, **values):
    """Set parameters on `node`. Returns a list of the ones that would not set.

    Deliberately not fatal. A wrong parameter name used to abort the build
    mid-way and leave a half-made component behind with a traceback and no
    other output — the operator then has to work out which of the three
    operators got configured before the exception. Collecting the failures and
    reporting them at the end leaves a component that is wrong in one known
    way instead of broken in an unknown one.

    It is still loud: `_report` prints every entry, and
    `tests/test_bootstrap.py` asserts the list is empty, so a bad name fails
    CI rather than degrading quietly.
    """
    failed = []
    for name, value in values.items():
        try:
            setattr(node.par, name, value)
        except (AttributeError, TypeError) as exc:
            failed.append("%s.par.%s = %r -> %s"
                          % (node.name, name, value, exc))
    return failed


def _dat_file_dir():
    """The folder of this script's own DAT, when it is file-backed.

    Dragging `bootstrap.py` into a TouchDesigner network makes a Text DAT whose
    **File** parameter is the path you dragged from — which is exactly the
    folder `td_mcp_bridge.py` sits in. That makes drag-then-Run-Script a
    zero-configuration install, and it is the only self-locating signal
    available to a script running inside a DAT.
    """
    try:
        par_file = me.par.file.eval()  # noqa: F821 - TD global
    except Exception:  # noqa: BLE001 - no `me`, no `file` par, or not a DAT
        return None
    if not par_file:
        return None
    path = str(par_file)
    if not os.path.isabs(path):
        try:
            path = os.path.join(str(project.folder), path)  # noqa: F821
        except Exception:  # noqa: BLE001
            return None
    return os.path.dirname(path)


def _real_file_dir():
    """`__file__`'s folder — only when it names a file that actually exists.

    Inside a Text DAT, `__file__` is the *operator path* (`/project1/bootstrap`),
    so `abspath()` turns it into `C:\\project1\\bootstrap` and every lookup
    beside it fails with a path that never existed. Checking `isfile` first is
    the whole fix.
    """
    try:
        candidate = __file__  # noqa: F821 - may be undefined under exec()
    except NameError:
        return None
    try:
        if os.path.isfile(candidate):
            return os.path.dirname(os.path.abspath(candidate))
    except (OSError, TypeError, ValueError):
        return None
    return None


def candidate_dirs(script_dir=None, repo=None):
    """Folders to search for `td_mcp_bridge.py`, best first.

    A repo root and its `bridge/` subfolder are both accepted wherever a path
    is given, because people reasonably point at either.
    """
    raw = []
    if script_dir:
        raw.append(script_dir)
    if repo:
        raw.append(repo)
    raw.append(_dat_file_dir())
    raw.append(_real_file_dir())
    try:
        raw.append(str(project.folder))  # noqa: F821 - TD global
    except Exception:  # noqa: BLE001
        pass
    try:
        raw.append(os.getcwd())
    except OSError:
        pass

    out = []
    for entry in raw:
        if not entry:
            continue
        for folder in (str(entry), os.path.join(str(entry), "bridge")):
            folder = os.path.normpath(folder)
            if folder not in out:
                out.append(folder)
    return out


def locate_bridge(script_dir=None, repo=None, bridge_path=None,
                  filename=BRIDGE_FILENAME):
    """Find `td_mcp_bridge.py`. Returns ``(path, text_or_None, searched)``.

    ``text`` is None when the file exists but this process cannot read it —
    still usable in `link` mode, where TouchDesigner does the reading.
    Raises when nothing was found, because both modes need the file and a
    component built without it is a broken component that looks installed.
    """
    if bridge_path:
        path = os.path.normpath(str(bridge_path))
        if not os.path.isfile(path):
            raise RuntimeError(_not_found_message([path]))
        return path, _read(path), [path]

    searched = []
    for folder in candidate_dirs(script_dir, repo):
        path = os.path.join(folder, filename)
        searched.append(path)
        if os.path.isfile(path):
            return path, _read(path), searched
    raise RuntimeError(_not_found_message(searched))


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _not_found_message(searched):
    lines = [
        "",
        "[td_mcp] Could not find %s." % BRIDGE_FILENAME,
        "",
        "Looked in:",
    ]
    lines += ["    %s" % p for p in searched]
    lines += [
        "",
        "Tell the builder where the td-mcp checkout is. In the Textport:",
        "",
        "    TD_MCP_REPO = r'C:/path/to/TD_MCP'",
        "    exec(open(TD_MCP_REPO + '/bridge/bootstrap.py').read())",
        "",
        "If you pasted this script into a Text DAT, a Textport variable is not",
        "visible here -- edit the REPO line in the CONFIGURATION block near the",
        "top of the DAT instead, then Run Script.",
        "",
        "Easiest of all: drag bootstrap.py from your file manager into the",
        "network and right-click the resulting DAT -> Run Script. The path is",
        "then read from the DAT's own File parameter.",
        "",
    ]
    return "\n".join(lines)


#: (name, kind, default, label) for the component's custom parameters.
CUSTOM_PARS = (
    ("Active", "Toggle", True, "Active"),
    ("Port", "Int", PORT, "Port"),
    ("Token", "Str", "", "Auth Token"),
    ("Allowexec", "Toggle", True, "Allow Python Execution"),
    ("Protectedpaths", "Str", "", "Protected Paths"),
    ("Restart", "Pulse", None, "Restart Bridge"),
    ("Printtoken", "Pulse", None, "Print Auth Token"),
)


def client_config(port, token, repo_dir):
    """The MCP client registration block for this component."""
    return {
        "mcpServers": {
            "td-mcp-live": {
                "command": "uv",
                "args": ["run", "--project", repo_dir.replace("\\", "/"),
                         "td-mcp-live", "--mcp"],
                "env": {"TD_MCP_AUTH_TOKEN": token,
                        "TD_MCP_PORT": str(port)},
            }
        }
    }


# ---------------------------------------------------------------------------
# The build (TouchDesigner only)
# ---------------------------------------------------------------------------
def build(target=TARGET, comp_name=COMP_NAME, port=PORT, mode=MODE,
          save_tox=SAVE_TOX, script_dir=None, repo=None, bridge_path=None):
    """Create or update the bridge component. Returns the COMP."""
    source_path, source_text, _searched = locate_bridge(
        script_dir=script_dir, repo=repo or REPO,
        bridge_path=bridge_path or BRIDGE_PATH)

    resolved_mode = resolve_mode(mode, source_text is not None, save_tox)
    if resolved_mode == "embed" and source_text is None:
        raise RuntimeError(
            "[td_mcp] cannot embed the bridge: found %s but could not read it "
            "(permissions? a disconnected network drive?). Use mode='link' to "
            "let TouchDesigner read it instead." % source_path)

    parent = op(target) or op("/project1") or root()
    comp = parent.op(comp_name)
    if comp is None:
        # COMP.create(opType, name) -- type FIRST.
        comp = parent.create("baseCOMP", comp_name)
        comp.nodeX, comp.nodeY = 0, -300
    comp.color = (0.16, 0.55, 0.75)
    comp.comment = "TD_MCP bridge — MCP server for this TouchDesigner session"

    _ensure_custom_pars(comp, port)

    problems = []

    bridge = comp.op("bridge") or comp.create("textDAT", "bridge")
    bridge.nodeX, bridge.nodeY = 0, 0
    if resolved_mode == "link":
        # Sync to File: TD reloads the DAT whenever the .py changes on disk,
        # so the file stays the single source of truth.
        problems += set_pars(bridge, file=source_path.replace("\\", "/"),
                             syncfile=True)
    else:
        problems += set_pars(bridge, syncfile=False, file="")
        bridge.text = source_text

    startup = comp.op("startup") or comp.create("executeDAT", "startup")
    startup.nodeX, startup.nodeY = 0, -150
    startup.text = STARTUP_CODE
    # Execute DAT, Execute page: `start` fires at TD launch, `create` when the
    # node is created (a dragged-in .tox never sees `start`), `exit` on quit.
    # `framestart` drives the main-thread pump: TouchDesigner objects cannot
    # be touched from the bridge's HTTP thread, so every request waits in a
    # queue that onFrameStart drains here, on the main thread.
    # `framestart` drives the main-thread pump; `playstatechange` drains the
    # queue the moment someone presses Play after a pause, when Frame Start
    # has not been firing.
    problems += set_pars(startup, active=True, start=True, create=True,
                         exit=True, framestart=True, playstatechange=True)

    control = comp.op("control") or comp.create("parameterexecuteDAT", "control")
    control.nodeX, control.nodeY = 0, -300
    control.text = PAREXEC_CODE
    # Parameter Execute DAT, Parameter Execute page. `op` names the operator
    # whose parameters are watched and `pars` which of them -- an empty `pars`
    # watches nothing, so the callbacks would never fire. The pulse toggle is
    # `onpulse`, not `pulse`: that guess raised
    # "'td.ParCollection' object has no attribute 'pulse'" on the first real
    # run (field-reported 2026-08-26).
    problems += set_pars(control, active=True, op="..", pars="*",
                         valuechange=True, onpulse=True)

    start_problems, started = _try_start(comp, startup)
    problems += start_problems

    saved = None
    if save_tox:
        saved = comp.save(_tox_path(comp_name), createFolders=True)

    _report(comp, resolved_mode, _effective_port(comp, port), source_path,
            saved, problems, started)
    return comp


def _try_start(comp, startup):
    """Start the bridge now rather than leaving it to a toggle.

    Two reasons. The auth token prints immediately, which is the thing the
    operator needs next. And a bridge that does not compile fails HERE, at
    build time, with one message that says where to look -- instead of the
    first real run, where the operator clicked `Active` and read six stacked
    tracebacks ending in "Module compilation error. See
    /project1/td_mcp/bridge for details."

    Returns ``(problems, started)``.
    """
    try:
        if not comp.par.Active.eval():
            return [], False
    except Exception:  # noqa: BLE001 - no Active par means nothing to start
        return [], False
    try:
        startup.module.start_bridge()
    except Exception as exc:  # noqa: BLE001
        return ["bridge did not start: %s -- open %s/bridge; TouchDesigner "
                "shows the compile error on that DAT. Pulse Restart Bridge "
                "once it is fixed." % (exc, comp.path)], False
    return [], True


def _effective_port(comp, fallback):
    """The port the component is actually configured with -- a rebuild keeps
    the operator's value, so the builder's argument may not be it."""
    try:
        return int(comp.par.Port.eval())
    except Exception:  # noqa: BLE001
        return int(fallback)


def _ensure_custom_pars(comp, port):
    """Add the component's parameters, preserving any value already set."""
    page = None
    for existing in comp.customPages:
        if existing.name == "TD MCP":
            page = existing
            break
    if page is None:
        page = comp.appendCustomPage("TD MCP")

    appenders = {"Toggle": page.appendToggle, "Int": page.appendInt,
                 "Str": page.appendStr, "Pulse": page.appendPulse}
    for name, kind, default, label in CUSTOM_PARS:
        if hasattr(comp.par, name):
            continue
        par_group = appenders[kind](name, label=label)
        if default is None:
            continue
        value = port if name == "Port" else default
        try:
            par_group[0].default = value
            par_group[0].val = value
        except Exception:  # noqa: BLE001 - a Pulse has no value to set
            pass


def _tox_path(comp_name):
    folder = getattr(project, "folder", "") or ""
    return os.path.join(folder, comp_name + ".tox").replace("\\", "/")


def _report(comp, mode, port, source_path, saved, problems=(), started=False):
    print("[td_mcp] component ready at %s (mode=%s)" % (comp.path, mode))
    if problems:
        # A parameter that would not set, or a bridge that would not start,
        # means the component is wrong in a specific, nameable way. Say which
        # rather than leaving the operator to find out when nothing answers.
        print("[td_mcp] WARNING: %d problem(s):" % len(problems))
        for entry in problems:
            print("[td_mcp]   %s" % entry)
        print("[td_mcp] the component exists but is not fully wired; "
              "please report this with your TouchDesigner build number")
    if mode == "link":
        print("[td_mcp] bridge DAT is synced to %s" % source_path)
        print("[td_mcp] edit that file and TouchDesigner reloads it; no re-paste")
    else:
        print("[td_mcp] bridge source embedded — the component is self-contained")
    if saved:
        print("[td_mcp] saved component to %s" % saved)
        print("[td_mcp] that .tox is now drag-and-drop on any machine")
    if started:
        token = None
        try:
            token = comp.op("bridge").module.AUTH_TOKEN
        except Exception:  # noqa: BLE001 - the bridge is up; token is a nicety
            pass
        try:
            version = comp.op("bridge").module.BRIDGE_VERSION
        except Exception:  # noqa: BLE001
            version = "?"
        print("[td_mcp] bridge v%s listening on http://127.0.0.1:%d" % (version, port))
        if token:
            print("[td_mcp] auth token: %s" % token)
            print("[td_mcp] set TD_MCP_AUTH_TOKEN to that value in your MCP client")
    elif not any("did not start" in p for p in problems):
        print("[td_mcp] Active is off; toggle it on the component to start the bridge")


def _inside_touchdesigner():
    """True when TouchDesigner's injected globals are present.

    Not a `__name__` check: this file is normally run as
    `exec(open(...).read())` in the Textport, where `__name__` is already
    `"__main__"`, so a `__name__` guard would do exactly the wrong thing in
    both directions — skipping the build in TouchDesigner and running it under
    pytest.
    """
    try:
        op  # noqa: B018,F821 - probing for TD's injected global
    except NameError:
        return False
    return True


if _inside_touchdesigner():
    try:
        build()
    except RuntimeError as _err:
        # A TouchDesigner traceback truncates and reformats the message, so
        # print the guidance plainly first — the whole point of that message is
        # that it can be read and acted on.
        print(str(_err))
        raise RuntimeError(
            "[td_mcp] bridge source not found — see the message above") from None
