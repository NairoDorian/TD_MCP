#!/usr/bin/env python3
"""Drive the TEST-01 TouchDesigner project from the command line.

The project's standing rule (AGENTS.md): **verify in TouchDesigner, do not
guess.** Any agent or developer working on TD_MCP may open the test project at
any moment to check a behaviour, a parameter name or a fix. This script makes
that one command:

    python scripts/td_live.py launch          # open TEST-01 with a known token
    python scripts/td_live.py status          # is the bridge answering?
    python scripts/td_live.py deploy          # copy bridge/ into TEST-01 + restart it
    python scripts/td_live.py call project_info
    python scripts/td_live.py call describe_op_type '{"op_type": "blurTOP"}'
    python scripts/td_live.py py "result = [c.name for c in op('/project1').children]"
    python scripts/td_live.py monitor 165hz   # TD + viewers on that screen ('primary',
                                              # 'secondary', 'DISPLAY1', 'index:N')
    python scripts/td_live.py close           # work done: save the project, quit TD
                                              # (--no-save to quit without saving)

Then `python scripts/live_smoke.py` exercises the MCP servers the way an agent
does (stdio MCP client -> td-mcp-live -> bridge -> TouchDesigner).

Configuration (all optional):
    TD_MCP_TEST_TOE      project to open (default ../TD_MCP_TESTS/TEST-01/NewProject.toe)
    TD_EXE               TouchDesigner.exe (default: newest under Program Files/Derivative)
    TD_MCP_AUTH_TOKEN    bridge token (default: generated once, kept in .td_mcp/live_token)
    TD_MCP_PORT          bridge port (default 9980)

The token only works if the td_mcp component's own `Token` parameter is empty
(a non-empty parameter overrides the environment). Standard library only.
"""

from __future__ import annotations

import glob
import http.client
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = ROOT / ".td_mcp" / "live_token"          # .td_mcp/ is gitignored
MONITOR_FILE = ROOT / ".td_mcp" / "monitor"           # the screen TD and its viewers use
DEFAULT_TOE = ROOT.parent / "TD_MCP_TESTS" / "TEST-01" / "NewProject.toe"
PORT = int(os.environ.get("TD_MCP_PORT", "9980"))


def toe_path() -> Path:
    """The test project to open (TD_MCP_TEST_TOE, else TEST-01's NewProject.toe).

    Verified 2026-11-23: with Increment Filename on, a save writes the base
    file AND a numbered copy (NewProject.toe + NewProject.11.toe, identical)
    and moves older numbered copies to Backup/ -- so the base name always
    holds the latest saved work, and the session reports the numbered name.
    """
    return Path(os.environ.get("TD_MCP_TEST_TOE") or DEFAULT_TOE)


def token() -> str:
    env = os.environ.get("TD_MCP_AUTH_TOKEN")
    if env:
        return env
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(24)
    TOKEN_FILE.write_text(tok, encoding="utf-8")
    return tok


def monitor_spec() -> str:
    """Which display TouchDesigner and its viewer windows go to (TD_MCP_MONITOR,
    else .td_mcp/monitor; '' = leave windows where they are)."""
    if os.environ.get("TD_MCP_MONITOR"):
        return os.environ["TD_MCP_MONITOR"]
    return MONITOR_FILE.read_text(encoding="utf-8").strip() if MONITOR_FILE.exists() else ""


def td_exe() -> str:
    if os.environ.get("TD_EXE"):
        return os.environ["TD_EXE"]
    found = sorted(glob.glob(r"C:/Program Files/Derivative/TouchDesigner*/bin/TouchDesigner.exe"))
    if not found:
        sys.exit("TouchDesigner.exe not found; set TD_EXE")
    return found[-1]


def call(tool: str, args: dict | None = None, timeout: float = 120) -> dict:
    body = json.dumps({"tool": tool, "args": args or {}}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/mcp", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token()}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except ValueError:
            return {"ok": False, "http_error": e.code}
    except (OSError, http.client.HTTPException, ValueError) as e:
        # A restarting bridge drops connections mid-request (RemoteDisconnected);
        # report it as "not answering" so `deploy` keeps polling instead of dying.
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def status() -> dict | None:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/status",
                                 headers={"Authorization": f"Bearer {token()}"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def wait_for(predicate, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        time.sleep(1)
    return False


def launch() -> None:
    if status():
        print(f"bridge already answering on :{PORT}")
        return
    toe = toe_path()
    if not toe.exists():
        sys.exit(f"test project not found: {toe} (set TD_MCP_TEST_TOE)")
    env = dict(os.environ, TD_MCP_AUTH_TOKEN=token(), TD_MCP_MONITOR=monitor_spec())
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([td_exe(), str(toe)], cwd=str(toe.parent), env=env,
                     creationflags=flags, close_fds=True)
    if not wait_for(lambda: status() is not None, 90):
        sys.exit("TouchDesigner started but the bridge did not answer within 90 s "
                 "(a license or startup dialog may be waiting)")
    print(json.dumps(call("project_info"), indent=1))
    if monitor_spec():
        time.sleep(4)  # TD restores its saved layout after start; the bridge re-places it
        print("TouchDesigner window is on:", where_is_td())


def where_is_td() -> str:
    """The display that holds the centre of TouchDesigner's main window."""
    # Computed inside TD from Win32 rectangles: TD's own ui.windowX/Y are Y-up
    # and cannot be compared with display rectangles.
    d = call("ui_state").get("td_on_display") or {}
    if not d:
        return "unknown"
    return "%s %s Hz%s" % (d.get("device"), d.get("hz"), " (primary)" if d.get("primary") else "")


def deploy() -> None:
    """Copy the repo's bridge into the test project and restart it there.

    The TEST-01 bridge DAT is linked (Sync to File) to the copy beside the
    .toe, so TouchDesigner recompiles on copy -- but the running server thread
    keeps the OLD code until a restart. Restarting from inside the request
    would deadlock (shutdown waits on the thread serving it), so the restart
    is deferred with run(..., delayFrames=20).
    """
    target = toe_path().parent
    for name in ("td_mcp_bridge.py", "td_mcp_agent.py"):
        shutil.copy2(ROOT / "bridge" / name, target / name)
    want = _repo_bridge_version()
    before = (status() or {}).get("started_at")
    # Load File first: TouchDesigner notices a changed file on its own
    # schedule, and a restart that fires before it does restarts the OLD code.
    call("execute_python", {"code": "op('/project1/td_mcp/bridge').par.loadonstartpulse.pulse()\n"
                                    "run(\"op('/project1/td_mcp').par.Restart.pulse()\", "
                                    "delayFrames=20)\nresult = 'scheduled'"})

    # The version alone cannot prove a restart: the old server answers with
    # the same version until it is replaced. A newer start time can -- the
    # restart came after the forced reload, so the new server runs the new file.
    def restarted():
        st = status() or {}
        return (st.get("bridge_version") == want and st.get("started_at") is not None
                and st.get("started_at") != before)

    ok = wait_for(restarted, 30)
    if ok:  # and the tool path answers, not just the socket (give it a moment)
        ok = wait_for(lambda: call("project_info").get("ok") is True, 10)
    print(f"bridge {want} {'running' if ok else 'NOT confirmed'} in {target.name}")
    if not ok:
        sys.exit(1)


def close(save: bool = True) -> None:
    """Finish a session: save the project, then quit TouchDesigner.

    The user's rule (AGENTS.md): when the testing or building work is done,
    save and close TD. The save runs synchronously so the reply can name the
    file TouchDesigner actually wrote (it may increment the name); the quit is
    deferred a few frames so this call gets its answer before TD exits.
    """
    if status() is None:
        print("no bridge answering -- TouchDesigner is not running (or not on this port)")
        return
    code = ("import os\n"
            "saved = project.save() if %s else None\n"
            "result = {'saved': bool(saved), 'file': project.folder + '/' + project.name,\n"
            "          'pid': os.getpid()}\n"
            "run('project.quit(force=True)', delayFrames=10)" % bool(save))
    reply = call("execute_python", {"code": code})
    info = reply.get("result") or {}
    if not reply.get("ok"):
        sys.exit("close failed: %s" % reply.get("error"))
    if save and not info.get("saved"):
        sys.exit("save failed -- TouchDesigner left open so nothing is lost")
    gone = wait_for(lambda: status() is None, 10)
    how = "closed"
    if not gone and info.get("pid"):
        # Verified 2025.33230: project.quit(force=True) called through the
        # bridge -- directly or deferred with run() -- returns and does nothing.
        # The project is saved, so end THIS TouchDesigner (the pid it reported
        # itself, never another instance).
        subprocess.run(["taskkill", "/PID", str(info["pid"]), "/F"] if os.name == "nt"
                       else ["kill", str(info["pid"])], capture_output=True, check=False)
        gone = wait_for(lambda: status() is None, 10)
        how = "closed (quit() is ignored from the bridge, so its process was ended after the save)"
    print("%s; TouchDesigner %s" % (
        "saved %s" % Path(info.get("file", "")).name if info.get("saved") else "not saved",
        how if gone else "did NOT close"))


def _repo_bridge_version() -> str:
    for line in (ROOT / "bridge" / "td_mcp_bridge.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("BRIDGE_VERSION = "):
            return line.split('"')[1]
    return ""


def main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "launch":
        launch()
    elif cmd == "status":
        print(json.dumps(status(), indent=1))
    elif cmd == "deploy":
        deploy()
    elif cmd == "close":
        close(save="--no-save" not in rest)
    elif cmd == "monitor":
        if rest:
            MONITOR_FILE.parent.mkdir(parents=True, exist_ok=True)
            MONITOR_FILE.write_text(rest[0], encoding="utf-8")
        print("TouchDesigner and its viewers go to: %s" % (monitor_spec() or "(wherever they open)"))
        if status() is not None:
            print("now on:", where_is_td())
    elif cmd == "call":
        args = json.loads(rest[1]) if len(rest) > 1 else {}
        print(json.dumps(call(rest[0], args), indent=1, default=str))
    elif cmd == "py":
        print(json.dumps(call("execute_python", {"code": rest[0]}), indent=1, default=str))
    else:
        sys.exit(f"unknown command {cmd!r}; see --help")


if __name__ == "__main__":
    main(sys.argv[1:])
