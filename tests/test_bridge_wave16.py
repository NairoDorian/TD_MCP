"""Wave 16: the safety controls the documentation promised, enforced.

Found by reading the code and confirmed on TouchDesigner 2025.33230 (TEST-01,
2026-11-28; UPSTREAM_SYNC_2026-11-28.md):

* ``TD_MCP_MAX_RISK`` / ``TD_BUILDER_LIVE_READONLY`` were documented but no
  code called the policy, so neither did anything.
* One ``GET`` with ``Accept: text/event-stream`` -- no token needed -- froze
  the bridge for good: its server was single-threaded, and the stream loop
  ignored the failed writes after the client left. ``/api/resource`` read the
  project without a token.
* ``save_tox`` wrote any path (the open ``.toe`` included); two snapshots in
  one second shared a name; deleting the parent of a protected path passed;
  five mutating tools were not fenced off the bridge's own component.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import threading
import time

import pytest

from td_mcp import server_live, server_offline
from td_mcp.tools import risk
from tests.td_double import build_bridge


@pytest.fixture()
def bridge():
    return build_bridge()


def _run(br, tool, args):
    return br["_run_tool"](tool, args)


# --------------------------------------------------------------------------
# Risk ceiling
# --------------------------------------------------------------------------
def test_bridge_risk_table_equals_the_registry(bridge):
    br, _td = bridge
    for tool in br["_DISPATCH"]:
        assert br["effective_risk"](tool, {}) == risk.risk_class(tool), tool
    for tool, args in (("build_network", {"replace": True}), ("annotate", {"action": "delete"}),
                       ("save_tox", {"overwrite": True}), ("annotate", {"action": "list"})):
        assert br["effective_risk"](tool, args) == risk.effective_class(tool, args)


def test_policy_rules():
    ro = {"TD_BUILDER_LIVE_READONLY": "1"}
    add = {"TD_MCP_MAX_RISK": "write_additive"}
    assert risk.policy_refusal("delete_node", {}, env={}) is None
    assert risk.policy_refusal("get_errors", {}, env=ro) is None
    assert risk.policy_refusal("create_node", {}, env=ro)["error_code"] == "policy"
    assert risk.policy_refusal("create_node", {}, env=add) is None
    for tool, args in (("delete_node", {}), ("execute_python", {}), ("exec_node_method", {}),
                       ("build_network", {"replace": True}), ("annotate", {"action": "delete"})):
        assert risk.policy_refusal(tool, args, env=add)["risk"] == "DESTRUCTIVE", tool
    refused = risk.policy_refusal("batch", {"ops": [{"tool": "get_node", "args": {}},
                                                    {"tool": "delete_node", "args": {}}]}, env=add)
    assert refused["tool"] == "delete_node" and "a step of batch" in refused["error"]
    assert risk.policy_refusal("batch", {"ops": [{"tool": "get_node"}]}, env=ro) is None
    # A typo fails closed.
    typo = risk.policy_refusal("create_node", {}, env={"TD_MCP_MAX_RISK": "WRITE_ADITIVE"})
    assert typo and "not one of" in typo["error"]
    # Offline tools that write files are covered too.
    assert risk.policy_refusal("td_project_edit", {}, env=ro)
    assert risk.policy_refusal("td_docs_search", {}, env=ro) is None
    steps = json.dumps([{"tool": "td_docs_search", "args": {}}, {"tool": "td_project_edit"}])
    assert risk.policy_refusal("td_tool_batch", {"ops": steps}, env=ro)["tool"] == "td_project_edit"


def test_the_bridge_enforces_the_ceiling_from_its_own_environment(bridge, monkeypatch):
    br, td = bridge
    td.make("n1", "noiseTOP")
    monkeypatch.setenv("TD_MCP_MAX_RISK", "WRITE_ADDITIVE")
    out = _run(br, "delete_node", {"path": "/project1/n1"})
    assert out["ok"] is False and out["error_code"] == "policy"
    assert td.project_comp.op("n1") is not None
    out = br["_do_batch"]([{"tool": "delete_node", "args": {"path": "/project1/n1"}}])
    assert out["batch"][0]["result"]["error_code"] == "policy"
    assert _run(br, "get_node", {"path": "/project1/n1"})["ok"] is True
    monkeypatch.setenv("TD_BUILDER_LIVE_READONLY", "1")
    assert _run(br, "set_node_color", {"path": "/project1/n1", "r": 1, "g": 0, "b": 0})["error_code"] == "policy"


def test_the_live_server_refuses_before_reaching_touchdesigner(monkeypatch):
    monkeypatch.setenv("TD_MCP_MAX_RISK", "WRITE_ADDITIVE")
    called = []
    monkeypatch.setattr(server_live.TDClient, "_call", lambda self, *a, **k: called.append(a) or {"ok": True},
                        raising=False)
    out = server_live.TOOL_REGISTRY["delete_node"]({"path": "/project1/x"})
    assert out["ok"] is False and out["error_code"] == "policy" and not called


def test_the_offline_batch_judges_each_step(monkeypatch):
    monkeypatch.setenv("TD_BUILDER_LIVE_READONLY", "1")
    out = json.loads(server_offline.td_tool_batch(json.dumps([
        {"tool": "td_project_edit", "args": {"file": "x.toe", "edits": []}}])))
    first = out["results"][0] if isinstance(out, dict) else out[0]
    assert first["result"]["error_code"] == "policy"


# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------
def test_more_tools_are_fenced_off_the_bridge(bridge, monkeypatch):
    br, td = bridge
    td.make("td_mcp", "baseCOMP")
    home = "/project1/td_mcp"
    monkeypatch.setitem(br, "_bridge_home", lambda: home)
    for tool, args in (("set_node_color", {"path": home, "r": 1, "g": 0, "b": 0}),
                       ("set_node_position", {"path": home, "x": 0, "y": 0}),
                       ("set_node_comment", {"path": home, "comment": "x"}),
                       ("auto_layout", {"path": home}),
                       ("build_and_verify", {"path": home, "type": "noiseTOP"})):
        assert _run(br, tool, args)["error_code"] == "self_path", tool


def test_deleting_the_parent_of_a_protected_path_is_refused(bridge, monkeypatch):
    br, td = bridge
    show = td.make("show", "baseCOMP")
    td.make("stage", "baseCOMP", parent=show)
    monkeypatch.setitem(br, "PROTECTED", ["/project1/show/stage"])
    out = br["_self_guard"]("/project1/show", "delete_node", ancestors=True)
    assert out and out["error_code"] == "protected" and "/project1/show/stage" in out["error"]
    assert br["_self_guard"]("/project1/show", "set_parameters", ancestors=False) is None


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------
def test_save_tox_never_overwrites_by_accident(bridge, tmp_path, monkeypatch):
    br, td = bridge
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))  # default folder
    td.make("fx", "baseCOMP")
    save = br["_do_save_tox"]
    assert save("/project1/fx", str(tmp_path / "fx.toe"))["error_code"] == "bad_path"
    target = tmp_path / "fx.tox"
    target.write_bytes(b"old")
    out = save("/project1/fx", str(target))
    assert out["error_code"] == "exists" and "fx-2.tox" in out["error"]
    out = save("/project1/fx", str(target), True)
    assert out["ok"] and out["replaced"] is True
    a, b = save("/project1/fx")["file"], None
    open(a, "wb").close()
    b = save("/project1/fx")["file"]
    assert a != b and b.endswith(".tox")


def test_snapshots_in_one_second_get_their_own_files(bridge, monkeypatch, tmp_path):
    br, td = bridge
    td.make("fx", "baseCOMP")
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    names = set()
    for _ in range(3):
        out = br["_do_snapshot_network"]("/project1/fx")
        open(out["snapshot"], "wb").close()  # the double's save() writes nothing
        names.add(out["snapshot"])
    assert len(names) == 3
    monkeypatch.setitem(br, "SNAPSHOT_KEEP", 2)
    out = br["_do_snapshot_network"]("/project1/fx")
    assert out.get("pruned_oldest", 0) >= 1 or len(os.listdir(tmp_path / "td_mcp_snapshots")) <= 3


# --------------------------------------------------------------------------
# HTTP: the real server, on a free port
# --------------------------------------------------------------------------
@pytest.fixture()
def server(bridge):
    br, _td = bridge
    br["AUTH_TOKEN"] = "tok"
    br["SSE_PING_SECONDS"] = 0.2
    handler = br["_Handler"]
    handler.AUTH_TOKEN = "tok"
    srv = br["_BridgeServer"](("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield br, srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _get(port, path, headers=None, timeout=5):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    c.request("GET", path, headers={"Host": "127.0.0.1", **(headers or {})})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body


def test_resource_reads_need_the_token(server):
    _br, port = server
    status, _ = _get(port, "/api/resource?uri=td://node//project1")
    assert status == 401


def test_an_open_stream_no_longer_blocks_the_bridge(server):
    br, port = server
    assert _get(port, "/", {"Accept": "text/event-stream"})[0] == 401   # no token, no stream
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: text/event-stream\r\n"
              b"Authorization: Bearer tok\r\n\r\n")
    got, deadline = b"", time.time() + 5
    while b"event: open" not in got and time.time() < deadline:
        got += s.recv(400)
    assert b"event: open" in got
    t0 = time.time()
    status, body = _get(port, "/api/status", timeout=3)
    assert status == 200 and time.time() - t0 < 2.5    # answered while the stream is open
    assert br["_open_streams"][0] == 1
    s.close()
    deadline = time.time() + 5
    while br["_open_streams"][0] and time.time() < deadline:
        time.sleep(0.1)
    assert br["_open_streams"][0] == 0                  # the loop noticed the client left


def test_gets_check_the_host_header(server):
    _br, port = server
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", "/api/status", headers={"Host": "evil.example"})
    assert c.getresponse().status == 403


def test_with_exec_off_only_snapshots_restore(bridge, monkeypatch, tmp_path):
    """A .tox can run Execute DATs when it loads: with the exec gate closed,
    restore_network loading any file was a way around it."""
    br, td = bridge
    td.make("fx", "baseCOMP")
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setitem(br, "ALLOW_EXEC", False)
    stranger = tmp_path / "elsewhere.tox"
    stranger.write_bytes(b"x")
    out = br["_do_restore_network"](str(stranger), "/project1")
    assert out["ok"] is False and out.get("code_sources")
    snap = br["_do_snapshot_network"]("/project1/fx")["snapshot"]
    open(snap, "wb").close()
    assert br["_do_restore_network"](snap, "/project1")["ok"] is True
    monkeypatch.setitem(br, "ALLOW_EXEC", True)
    assert br["_do_restore_network"](str(stranger), "/project1")["ok"] is True


def test_a_restart_reads_the_panel_settings_again(bridge, monkeypatch):
    """The panel writes Allow Python Execution / Protected Paths into the
    environment and restarts the bridge; the module used to read them only at
    import, so switching exec off did nothing until TouchDesigner restarted."""
    br, _td = bridge
    assert br["ALLOW_EXEC"] is True
    monkeypatch.setenv("TD_MCP_ALLOW_EXEC", "0")
    monkeypatch.setenv("TD_MCP_EXEC_MODE", "off")
    monkeypatch.setenv("TD_MCP_PROTECTED_PATHS", "/project1/show, /project1/out")
    monkeypatch.setenv("TD_MCP_AUTH_TOKEN", "pinned")
    br["reload_settings"]()
    assert br["ALLOW_EXEC"] is False and br["EXEC_MODE"] == "off"
    assert br["PROTECTED"] == ["/project1/show", "/project1/out"]
    assert br["AUTH_TOKEN"] == "pinned"
    assert br["_do_execute_python"]("result = 1")["ok"] is False
