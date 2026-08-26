"""Handler-level tests for the in-TD bridge, run against a TD API double.

Until Wave 12 the only tested part of ``bridge/td_mcp_bridge.py`` was the
~90-line "pure helpers" block; the ~1,200 lines of ``_do_*`` handlers that
touch TouchDesigner were never executed by any test. That is the gap in which
``_do_get_errors`` shipped reading ``node.errors`` as an iterable attribute
even though ``OP_Class`` documents::

    errors(recurse=False) -> str

The regression tests in `TestDiagnosticSurfaces` fail hard against the old
code: `get_errors` raised ``TypeError: 'method' object is not iterable``, and
the scanners swallowed the same exception and reported clean networks.
"""

from __future__ import annotations

import ast
import importlib
import json
import urllib.error
import urllib.request

import pytest

import threading
import time

from tests.td_double import (
    BRIDGE_PATH,
    FakeParMode,
    Pumper,
    TDEnv,
    block_td_mcp,
    build_bridge,
)


@pytest.fixture()
def bridge():
    br, td = build_bridge()
    return br, td


# ---------------------------------------------------------------------------
# The bug this wave exists to kill
# ---------------------------------------------------------------------------
class TestDiagnosticSurfaces:
    def test_cook_error_is_reported(self, bridge):
        br, td = bridge
        td.make("noise1", "noiseTOP", errors="Cannot cook: no input")
        out = br["_do_get_errors"]("/project1/noise1")
        assert out["ok"] is True
        assert out["errors"] == ["Cannot cook: no input"]
        assert out["error_count"] == 1
        assert out["hasErrors"] is True

    def test_errors_is_called_not_indexed(self, bridge):
        """The exact failure: a bound method is truthy, so `or []` never fires
        and `list(...)` raises. A clean call must return a real list."""
        br, td = bridge
        node = td.make("noise1", "noiseTOP", errors="boom")
        assert callable(node.errors), "the double must model errors() as a method"
        out = br["_do_get_errors"]("/project1/noise1")
        assert "error" not in out or out.get("ok") is True
        assert out["errors"] == ["boom"]

    def test_script_error_is_the_third_surface(self, bridge):
        """A DAT whose onCook raises shows red in the network while errors()
        stays empty. Reading two of the surfaces called that operator clean."""
        br, td = bridge
        td.make("text1", "textDAT",
                scriptErrors="/project1/text1: Traceback (most recent call last):\n"
                             '  File "<DAT>", line 3\n'
                             "    x = y\n"
                             "NameError: name 'y' is not defined")
        out = br["_do_get_errors"]("/project1/text1")
        assert out["cook_errors"] == []
        assert len(out["script_errors"]) == 1
        assert out["hasErrors"] is True
        assert out["errorCount"] == 1
        kinds = {d["kind"] for d in out["diagnostics"]}
        assert kinds == {"script"}

    def test_traceback_counts_as_one_error_not_one_per_line(self, bridge):
        br, td = bridge
        td.make("text1", "textDAT",
                scriptErrors="Traceback (most recent call last):\n"
                             '  File "<DAT>", line 1\n'
                             "  File \"<DAT>\", line 9\n"
                             "ZeroDivisionError: division by zero")
        out = br["_do_get_errors"]("/project1/text1")
        assert out["errorCount"] == 1, out["script_errors"]
        assert "ZeroDivisionError" in out["script_errors"][0]

    def test_two_tracebacks_are_two_errors(self, bridge):
        br, td = bridge
        td.make("text1", "textDAT",
                scriptErrors="Traceback (most recent call last):\n"
                             "  File \"<DAT>\", line 1\n"
                             "NameError: a\n"
                             "Traceback (most recent call last):\n"
                             "  File \"<DAT>\", line 2\n"
                             "TypeError: b")
        out = br["_do_get_errors"]("/project1/text1")
        assert out["errorCount"] == 2

    def test_glsl_compile_error_comes_from_the_docked_info_dat(self, bridge):
        """op.errors() reports nothing for a shader that failed to compile —
        TD writes the real message to the auto-docked Info DAT."""
        br, td = bridge
        glsl = td.make("glsl1", "glslTOP")
        td.dock_info(glsl, "Compile Errors: \n"
                           "0(12) : error C1008: undefined variable \"half\"")
        out = br["_do_get_errors"]("/project1/glsl1")
        assert out["cook_errors"] == []
        assert len(out["shader_errors"]) == 1
        assert "C1008" in out["shader_errors"][0]
        assert out["hasErrors"] is True

    def test_info_dat_discovery_is_by_type_not_name(self, bridge):
        """TD keeps the OLD dock name after a host rename, so name matching
        silently stops working. Discovery must key on dat.type == 'info'."""
        br, td = bridge
        glsl = td.make("glsl1", "glslTOP")
        info = td.dock_info(glsl, "0(3) : error C0000: syntax error")
        info.name = "some_stale_name_from_before_the_rename"
        out = br["_do_get_errors"]("/project1/glsl1")
        assert len(out["shader_errors"]) == 1

    def test_healthy_info_dat_yields_no_shader_errors(self, bridge):
        br, td = bridge
        glsl = td.make("glsl1", "glslTOP")
        td.dock_info(glsl, "Resolution: 1280 x 720\nCook Time: 0.4\nErrors: 0")
        out = br["_do_get_errors"]("/project1/glsl1")
        assert out["shader_errors"] == []
        assert out["hasErrors"] is False

    def test_shader_pass_can_be_skipped(self, bridge):
        """Reading an Info DAT cooks it, so callers must be able to opt out."""
        br, td = bridge
        glsl = td.make("glsl1", "glslTOP")
        td.dock_info(glsl, "0(1) : error C0000: syntax error")
        out = br["_do_get_errors"]("/project1/glsl1", True, False)
        assert out["shader_errors"] == []

    def test_warnings_are_not_errors(self, bridge):
        br, td = bridge
        td.make("n1", "noiseTOP", warnings="Resolution differs from input")
        out = br["_do_get_errors"]("/project1/n1")
        assert out["error_count"] == 0
        assert out["warning_count"] == 1
        assert out["hasErrors"] is False

    def test_all_surfaces_merge_into_one_error_list(self, bridge):
        br, td = bridge
        glsl = td.make("glsl1", "glslTOP", errors="Cannot cook",
                       scriptErrors="RuntimeError: nope",
                       warnings="deprecated")
        td.dock_info(glsl, "0(2) : error C7011: implicit cast")
        out = br["_do_get_errors"]("/project1/glsl1")
        assert out["errorCount"] == 3
        assert out["warning_count"] == 1
        assert {d["kind"] for d in out["diagnostics"]} == {
            "cook", "script", "shader", "warning"}

    def test_missing_node(self, bridge):
        br, _ = bridge
        assert br["_do_get_errors"]("/nope")["ok"] is False


class TestClearScriptErrors:
    def test_clears_a_stale_traceback(self, bridge):
        br, td = bridge
        node = td.make("text1", "textDAT", scriptErrors="NameError: stale")
        out = br["_do_clear_script_errors"]("/project1/text1")
        assert out["ok"] is True
        assert out["cleared"] == 1
        assert node.scriptErrors() == ""
        assert br["_do_get_errors"]("/project1/text1")["hasErrors"] is False

    def test_missing_node(self, bridge):
        br, _ = bridge
        assert br["_do_clear_script_errors"]("/nope")["ok"] is False


# ---------------------------------------------------------------------------
# The readers that used to swallow the same exception
# ---------------------------------------------------------------------------
class TestScannersSeeErrors:
    def test_scan_network_reports_errors(self, bridge):
        br, td = bridge
        td.make("bad1", "noiseTOP", errors="Cannot cook: no input")
        out = br["_do_scan_network"]("/project1", 2)
        found = [n for n in out["nodes"] if n["name"] == "bad1"]
        assert found and found[0]["errors"] == ["Cannot cook: no input"]

    def test_get_node_reports_errors(self, bridge):
        br, td = bridge
        td.make("bad1", "noiseTOP", errors="Cannot cook")
        assert br["_do_get_node"]("/project1/bad1")["errors"] == ["Cannot cook"]

    def test_validate_network_reports_errors(self, bridge):
        br, td = bridge
        td.make("bad1", "noiseTOP", errors="Cannot cook")
        out = br["_do_validate_network"]("/project1", 2)
        cook = [i for i in out["issues"] if i["issue"].startswith("cook error")]
        assert len(cook) == 1

    def test_scanners_see_script_errors_too(self, bridge):
        br, td = bridge
        td.make("t1", "textDAT", scriptErrors="NameError: boom")
        assert br["_do_get_node"]("/project1/t1")["errors"] == ["NameError: boom"]

    def test_clean_network_stays_clean(self, bridge):
        br, td = bridge
        td.make("ok1", "noiseTOP")
        assert br["_do_get_node"]("/project1/ok1")["errors"] == []


class TestBuildAndVerify:
    def test_reports_a_script_error_from_the_write_that_caused_it(self, bridge):
        br, td = bridge
        created = {}

        real_create = br["_do_create"]

        def create_and_break(path, op_type, name=None, x=None, y=None):
            r = real_create(path, op_type, name, x, y)
            node = td.root.op(r["path"]) if r.get("ok") else None
            if node is not None:
                node._scriptErrors = "TypeError: bad callback"
                created["node"] = node
            return r

        br["_do_create"] = create_and_break
        out = br["_do_build_and_verify"]("/project1", "textDAT", "t9")
        assert out["hasErrors"] is True
        assert out["script_errors"] == ["TypeError: bad callback"]


class TestBindParameterExpression:
    def test_new_cook_error_is_reported(self, bridge):
        """`op_errors` was structurally unable to be non-empty before Wave 12."""
        br, td = bridge
        node = td.make("n1", "noiseTOP", params={"period": 1.0})

        original = node.errors

        def errors_after_bind(recurse=False):
            if node.par.period.mode is FakeParMode.EXPRESSION:
                return "Invalid expression: name 'nope' is not defined"
            return original(recurse)

        node.errors = errors_after_bind
        out = br["_do_bind_parameter_expression"]("/project1/n1", "period", "nope")
        assert out["ok"] is True
        assert out["op_errors"] == [
            "Invalid expression: name 'nope' is not defined"]

    def test_clean_expression_reports_no_new_errors(self, bridge):
        br, td = bridge
        td.make("n1", "noiseTOP", params={"period": 1.0})
        out = br["_do_bind_parameter_expression"]("/project1/n1", "period",
                                                  "absTime.seconds")
        assert out["op_errors"] == []

    def test_unknown_parameter(self, bridge):
        br, td = bridge
        td.make("n1", "noiseTOP")
        out = br["_do_bind_parameter_expression"]("/project1/n1", "nope", "1")
        assert out["ok"] is False


# ---------------------------------------------------------------------------
# Pure parsers, exercised directly
# ---------------------------------------------------------------------------
class TestGroupDiagnostics:
    def test_empty(self, bridge):
        br, _ = bridge
        assert br["group_diagnostics"]("") == []
        assert br["group_diagnostics"](None) == []

    def test_plain_lines_are_separate(self, bridge):
        br, _ = bridge
        assert br["group_diagnostics"]("a\nb\nc") == ["a", "b", "c"]

    def test_blank_lines_dropped(self, bridge):
        br, _ = bridge
        assert br["group_diagnostics"]("a\n\n\nb") == ["a", "b"]

    def test_traceback_stays_whole(self, bridge):
        br, _ = bridge
        text = ("Traceback (most recent call last):\n"
                '  File "x", line 1\n'
                "ValueError: nope\n"
                "some other error")
        groups = br["group_diagnostics"](text)
        assert len(groups) == 2
        assert groups[0].endswith("ValueError: nope")
        assert groups[1] == "some other error"


class TestShaderErrorLines:
    def test_telemetry_is_not_an_error(self, bridge):
        br, _ = bridge
        assert br["shader_error_lines"](
            "Resolution: 1280 x 720\nCook Time: 0.4\nErrors: 0") == []

    def test_compile_error_detected(self, bridge):
        br, _ = bridge
        out = br["shader_error_lines"](
            '0(12) : error C1008: undefined variable "half"')
        assert len(out) == 1

    def test_empty(self, bridge):
        br, _ = bridge
        assert br["shader_error_lines"]("") == []


# ---------------------------------------------------------------------------
# The double itself must keep modelling TD faithfully
# ---------------------------------------------------------------------------
class TestDoubleFidelity:
    def test_error_accessors_are_methods_returning_str(self):
        td = TDEnv()
        node = td.make("n", "noiseTOP", errors="a\nb")
        assert isinstance(node.errors(), str)
        assert isinstance(node.warnings(), str)
        assert isinstance(node.scriptErrors(), str)

    def test_findchildren_depth_is_exact_not_maximum(self):
        """TD matches `depth` as an EXACT relative depth. A handler that reads
        it as "up to N" silently matches nothing — which is how an entire
        cross-build repair path in Embody was a no-op with no test coverage."""
        td = TDEnv()
        outer = td.make("outer", "containerCOMP")
        inner = td.make("inner", "containerCOMP", parent=outer)
        td.make("leaf", "noiseTOP", parent=inner)
        assert [c.name for c in outer.findChildren(depth=1)] == ["inner"]
        assert [c.name for c in outer.findChildren(depth=2)] == ["leaf"]
        assert outer.findChildren(depth=-1) == []

    def test_pop_points_ignores_slicing_arguments(self):
        """POP.points(attr, start, count) accepts and ignores both."""
        td = TDEnv()
        pop = td.make("p1", "noisePOP")
        pop._attributes = {"P": [[0, 0, 0]] * 10}
        assert len(pop.points("P", 0, 3)) == 10


class TestSetParameters:
    """`set_parameters` is among the most-used tools and had no handler test.

    The double could not even express one until it modelled `op.par['name']`,
    which is the form the bridge actually uses at three call sites.
    """

    def test_sets_a_value(self, bridge):
        br, td = bridge
        node = td.make("n1", "noiseTOP", params={"period": 1.0})
        out = br["_do_set_parameters"]("/project1/n1", {"period": 4.0})
        assert out["ok"] is True
        assert node.par.period.val == 4.0

    def test_reports_an_unknown_parameter_rather_than_silently_passing(self, bridge):
        br, td = bridge
        td.make("n1", "noiseTOP", params={"period": 1.0})
        out = br["_do_set_parameters"]("/project1/n1", {"perriod": 4.0})
        rendered = json.dumps(out)
        assert "perriod" in rendered

    def test_a_close_match_is_suggested(self, bridge):
        """A misspelling should cost one round-trip, not a debugging session."""
        br, td = bridge
        td.make("n1", "noiseTOP", params={"period": 1.0})
        out = br["_do_set_parameters"]("/project1/n1", {"perriod": 4.0})
        assert "period" in json.dumps(out)

    def test_missing_node(self, bridge):
        br, _ = bridge
        assert br["_do_set_parameters"]("/nope", {"x": 1})["ok"] is False


class TestParCollectionFidelity:
    """The double must refuse what TouchDesigner refuses.

    `control.par.pulse = True` shipped because the double quietly created the
    attribute. TouchDesigner answers
    `'td.ParCollection' object has no attribute 'pulse'`.
    """

    def test_assigning_an_unknown_parameter_raises(self):
        env = TDEnv()
        node = env.make("n1", "noiseTOP", params={"period": 1.0})
        with pytest.raises(AttributeError, match="ParCollection"):
            node.par.nosuchpar = 1

    def test_reading_an_unknown_parameter_raises(self):
        env = TDEnv()
        node = env.make("n1", "noiseTOP")
        with pytest.raises(AttributeError):
            node.par.nosuchpar

    def test_indexing_an_unknown_parameter_returns_none(self):
        """ParCollection_Class: `par['name']` -- "If not found None is
        returned." This test used to expect an exception, because the double
        had been written from the bridge rather than from the docs."""
        env = TDEnv()
        node = env.make("n1", "noiseTOP")
        assert node.par["nosuchpar"] is None

    def test_a_known_parameter_sets_both_ways(self):
        env = TDEnv()
        node = env.make("n1", "noiseTOP", params={"period": 1.0})
        node.par.period = 2.0
        assert node.par["period"].val == 2.0


# ---------------------------------------------------------------------------
# The bridge must be self-contained (field-reported, 2026-08-26)
#
#   DAT compile error: /project1/td_mcp/bridge
#   NameError: name 'StreamableHTTPMixin' is not defined
#
# `from td_mcp.streamable_http import StreamableHTTPMixin` sat in a try/except
# that set a flag on failure, and the handler class then inherited from the
# missing name unconditionally. Inside TouchDesigner td_mcp is never importable,
# so the bridge had never compiled there. Pytest runs with td_mcp importable,
# which is why 600 tests said otherwise. The double now blocks it.
# ---------------------------------------------------------------------------
class TestSelfContained:
    def test_the_double_really_blocks_td_mcp(self):
        """The guard would be vacuous if it passed for the wrong reason."""
        with block_td_mcp():
            with pytest.raises(ImportError):
                importlib.import_module("td_mcp")
            with pytest.raises(ImportError):
                importlib.import_module("td_mcp.tools.recovery")
        importlib.import_module("td_mcp")  # and it is restored afterwards

    def test_bridge_source_imports_nothing_from_td_mcp(self):
        tree = ast.parse(open(BRIDGE_PATH, encoding="utf-8").read())
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] == "td_mcp":
                    offenders.append(f"line {node.lineno}: from {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "td_mcp":
                        offenders.append(f"line {node.lineno}: import {alias.name}")
        assert offenders == []

    def test_the_real_bridge_compiles_as_a_dat_module(self):
        """`DAT.module` on the actual source: td_mcp blocked, `__file__` set to
        the operator path -- the closest the double gets to TouchDesigner."""
        env = TDEnv()
        dat = env.make("bridge", "textDAT")
        dat.text = open(BRIDGE_PATH, encoding="utf-8").read()
        mod = dat.module
        for name in ("start", "stop", "status", "AUTH_TOKEN", "_DISPATCH",
                     "_Handler"):
            assert hasattr(mod, name), name
        assert mod.status()["running"] is False

    def test_a_bridge_needing_td_mcp_fails_the_way_touchdesigner_reports_it(self):
        env = TDEnv()
        dat = env.make("bridge", "textDAT")
        dat.text = "from td_mcp.streamable_http import StreamableHTTPMixin\n"
        with pytest.raises(RuntimeError, match="Module compilation error"):
            dat.module

    def test_session_helpers_exist_and_round_trip(self, bridge):
        """`_get_or_create_session` called three helpers the bridge never
        defined -- a NameError waiting on the first JSON-RPC request."""
        br, _ = bridge
        cls = br["_Handler"]
        handler = cls.__new__(cls)
        handler.headers = {}
        sid, session = handler._get_or_create_session()
        assert sid and session["last_seen"]
        handler.headers = {"Mcp-Session-Id": sid}
        sid2, session2 = handler._get_or_create_session()
        assert sid2 == sid and session2 is session

    def test_expired_sessions_are_purged(self, bridge):
        br, _ = bridge
        br["_touch_session"]("old", "tok")
        br["_sessions"]["old"]["last_seen"] -= br["SESSION_TTL"] + 1
        assert br["_get_session"]("old") is None


class TestCreateParent:
    def test_an_unresolvable_parent_is_reported_not_a_crash(self, bridge):
        """`parent = _op(path) or parent()` -- assigning `parent` makes it a
        local for the whole function, so the fallback call raised
        UnboundLocalError. Only on the error path, which is how it survived."""
        br, _ = bridge
        out = br["_do_create"]("/nope/nowhere", "noiseTOP")
        assert out["ok"] is False
        assert "parent not found" in out["error"]

    def test_no_path_means_the_bridge_component_parent(self, bridge):
        br, _ = bridge
        assert br["_do_create"](None, "noiseTOP")["ok"] is True

    def test_restore_and_import_share_the_fix(self, bridge):
        br, _ = bridge
        assert br["_do_import_recipe"]([], "/nope")["ok"] is False
        out = br["_do_restore_network"]("x.tox", "/nope")
        assert "parent not found" in out["error"]


class TestLiveTransport:
    """The whole HTTP stack: a real socket, the real handler, the double
    behind it. The closest thing to a session in TouchDesigner that a test
    can run."""

    @pytest.fixture()
    def server(self, monkeypatch):
        monkeypatch.setenv("TD_MCP_PORT", "0")  # ephemeral
        br, td = build_bridge()
        td.make("noise1", "noiseTOP", errors="Cannot cook")
        br["start"]()
        try:
            # TD objects may only be touched from the main thread; the HTTP
            # thread queues and a per-frame pump runs the handler. The Pumper
            # stands in for TouchDesigner's frame loop.
            with Pumper(br["pump"]):
                port = br["_server"].server_address[1]
                yield br, td, f"http://127.0.0.1:{port}", br["AUTH_TOKEN"]
        finally:
            br["stop"]()

    @staticmethod
    def _post(base, path, body, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(base + path,
                                     data=json.dumps(body).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")

    def test_legacy_mcp_endpoint_dispatches_a_tool(self, server):
        _, _, base, token = server
        status, body = self._post(base, "/mcp", {
            "tool": "get_errors", "args": {"path": "/project1/noise1"}}, token)
        assert status == 200 and body["ok"] is True
        assert body["errors"] == ["Cannot cook"]

    def test_wrong_token_is_refused(self, server):
        _, _, base, _ = server
        status, _ = self._post(base, "/mcp",
                               {"tool": "project_info", "args": {}}, "nope")
        assert status == 401

    def test_a_refused_request_still_reads_its_body(self, server):
        """Answering a 401 with the body unread makes Windows RST the socket;
        the client then sees '[WinError 10053] connection aborted' instead of
        the status. It surfaced as a flaky test; it is a real bug."""
        _, _, base, _ = server
        big = {"tool": "set_dat_text", "args": {"path": "/x", "text": "x" * 200_000}}
        for _ in range(5):
            status, body = self._post(base, "/mcp", big, "nope")
            assert status == 401 and body["error"] == "unauthorized"

    def test_json_rpc_initialize_negotiates_over_the_wire(self, server):
        _, _, base, token = server
        status, body = self._post(base, "/", {
            "jsonrpc": "2.0", "id": 7, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"}}, token)
        assert status == 200
        assert body["result"]["protocolVersion"] == "2025-06-18"

    def test_json_rpc_tools_call_reaches_the_double(self, server):
        _, _, base, token = server
        status, body = self._post(base, "/", {
            "jsonrpc": "2.0", "id": 8, "method": "tools/call",
            "params": {"name": "get_errors",
                       "arguments": {"path": "/project1/noise1"}}}, token)
        assert status == 200
        assert "Cannot cook" in body["result"]["content"][0]["text"]

    def test_a_tool_call_runs_inside_a_named_undo_block(self, server):
        """The first live session: every call, read-only included, died on
        `with ui.undo:` -- TD's Undo object is not a context manager. It has
        startBlock(name)/endBlock(), and a call must open and close exactly
        one block so a single Ctrl+Z reverts the whole thing."""
        br, td, base, token = server
        before = td.ui.undo.blocks
        self._post(base, "/mcp", {"tool": "create_node",
                                  "args": {"path": "/project1", "type": "noiseTOP",
                                           "name": "undo_me"}}, token)
        assert td.ui.undo.blocks == before + 1
        assert td.ui.undo.names[-1] == "td_mcp: create_node"
        assert td.ui.undo.depth == 0  # closed again


class TestUndoFidelity:
    def test_the_double_refuses_the_context_manager_protocol(self):
        """If this ever passes for `with`, the double has drifted back to
        modelling the bug instead of TouchDesigner."""
        env = TDEnv()
        with pytest.raises((AttributeError, TypeError)):
            with env.ui.undo:
                pass

    def test_batch_opens_one_block_per_op(self, bridge):
        br, td = bridge
        before = td.ui.undo.blocks
        br["_do_batch"]([{"tool": "create_node", "args": {"path": "/project1", "type": "noiseTOP"}},
                         {"tool": "create_node", "args": {"path": "/project1", "type": "levelTOP"}}])
        assert td.ui.undo.blocks == before + 2
        assert td.ui.undo.depth == 0

    def test_a_missing_undo_api_does_not_break_the_tool(self, bridge):
        """TouchEngine and headless hosts have no ui.undo; the tool must still
        run, it just gets no undo step."""
        br, td = bridge
        del td.ui.undo
        out = br["_do_batch"]([{"tool": "project_info", "args": {}}])
        assert out["batch"][0]["result"]["ok"] is True


class TestLinkModeReload:
    """In link mode TouchDesigner recompiles the DAT when the file changes,
    but the previous module's serve_forever thread keeps the port. Without
    adoption, `stop()` on the new module did nothing and `start()` failed
    with "address already in use"."""

    @staticmethod
    def _free_port():
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def test_a_new_module_load_adopts_the_previous_server(self, monkeypatch):
        port = self._free_port()
        monkeypatch.setenv("TD_MCP_PORT", str(port))
        first, _ = build_bridge()
        first["start"]()
        old_server = first["_server"]
        try:
            second, _ = build_bridge()          # the "reloaded" module
            assert second["_server"] is None    # it knows nothing of the old one
            second["start"]()                   # must not raise "address in use"
            assert second["_server"].server_address[1] == port
            assert old_server.socket.fileno() == -1  # the old one was closed
        finally:
            second["stop"]()
            first["stop"]()

    def test_stop_on_a_fresh_module_adopts_an_orphan(self, monkeypatch):
        port = self._free_port()
        monkeypatch.setenv("TD_MCP_PORT", str(port))
        first, _ = build_bridge()
        first["start"]()
        try:
            second, _ = build_bridge()
            second["stop"]()                    # Restart Bridge after a reload
            assert first["_server"].socket.fileno() == -1
        finally:
            first["stop"]()


# ---------------------------------------------------------------------------
# Main-thread marshalling (field-reported, first live session)
#
#   TouchDesigner objects cannot be accessed outside the main thread.
#   ... Application may behave unpredictably or terminate.
#
# The HTTP server runs on its own thread, and every handler touched TD from
# it. Now the HTTP thread only queues; the component's Execute DAT drains the
# queue on Frame Start and the handler runs there.
# ---------------------------------------------------------------------------
class TestMainThread:
    def test_a_tool_runs_on_the_pumping_thread_not_the_http_thread(self, monkeypatch):
        monkeypatch.setenv("TD_MCP_PORT", "0")
        br, _ = build_bridge()
        br["_DISPATCH"]["_which_thread"] = lambda a: {
            "ok": True, "thread": threading.current_thread().name}
        br["start"]()
        try:
            with Pumper(br["pump"]) as pumper:
                port = br["_server"].server_address[1]
                status, body = TestLiveTransport._post(
                    f"http://127.0.0.1:{port}", "/mcp",
                    {"tool": "_which_thread", "args": {}}, br["AUTH_TOKEN"])
            assert status == 200
            assert body["thread"] == pumper.thread.name
        finally:
            br["stop"]()

    def test_without_a_pump_a_request_fails_fast_with_a_diagnosis(self, monkeypatch):
        """No Frame Start callback draining the queue -- the request must not
        hang for the full timeout, and must say what to fix."""
        monkeypatch.setenv("TD_MCP_PORT", "0")
        br, _ = build_bridge()
        br["start"]()
        try:
            port = br["_server"].server_address[1]
            t0 = time.time()
            status, body = TestLiveTransport._post(
                f"http://127.0.0.1:{port}", "/mcp",
                {"tool": "project_info", "args": {}}, br["AUTH_TOKEN"])
            assert status == 500
            assert "pump has never run" in body["error"]
            assert "bootstrap.py" in body["error"]
            assert time.time() - t0 < br["PUMP_STALL_SECONDS"] + 3
        finally:
            br["stop"]()

    def test_a_stalled_pump_names_the_reload_case(self, bridge):
        br, _ = bridge
        br["_last_pump"] = time.time() - 60
        assert "Restart Bridge" in br["_pump_diagnosis"]()

    def test_inline_when_already_on_the_main_thread(self, bridge):
        """A tool calling a tool, or a Textport call, must not deadlock on
        its own queue."""
        br, _ = bridge
        assert br["on_main_thread"]() is True
        assert br["_invoke"]("project_info", {})["ok"] is True
        assert br["_JOBS"].qsize() == 0

    def test_a_handler_exception_is_re_raised_on_the_caller_thread(self, monkeypatch):
        monkeypatch.setenv("TD_MCP_PORT", "0")
        br, _ = build_bridge()
        br["_DISPATCH"]["_boom"] = lambda a: 1 / 0
        br["start"]()
        try:
            with Pumper(br["pump"]):
                port = br["_server"].server_address[1]
                status, body = TestLiveTransport._post(
                    f"http://127.0.0.1:{port}", "/mcp",
                    {"tool": "_boom", "args": {}}, br["AUTH_TOKEN"])
            assert status == 500 and "division by zero" in body["error"]
        finally:
            br["stop"]()

    def test_pump_is_time_budgeted(self, bridge):
        """One slow job must not be followed by a second in the same frame."""
        br, _ = bridge
        br["PUMP_BUDGET_MS"] = 1.0
        ran = []
        for i in range(3):
            br["_JOBS"].put(br["_Job"](lambda i=i: (time.sleep(0.005), ran.append(i)), (), {}))
        assert br["pump"]() == 1
        assert br["pump"]() == 1

    def test_status_reports_pump_health_and_version(self, bridge):
        br, _ = bridge
        assert br["status"]()["pump_alive"] is False
        br["pump"]()
        st = br["status"]()
        assert st["pump_alive"] is True
        assert st["version"] == br["BRIDGE_VERSION"]


class TestProjectAndTimeline:
    """`project.cook` does not exist. fps is `project.cookRate`; the timeline
    is the Time COMP at `root.time` (frame/seconds/rate/play/start/end)."""

    def test_project_info_uses_cook_rate(self, bridge):
        br, _ = bridge
        out = br["_do_project_info"]()
        assert out["ok"] is True and out["fps"] == 60.0
        assert out["bridge_version"] == br["BRIDGE_VERSION"]

    def test_get_performance_uses_cook_rate(self, bridge):
        br, _ = bridge
        assert br["_do_get_performance"]("/project1")["fps"] == 60.0

    def test_timeline_addresses_root_time(self, bridge):
        br, td = bridge
        out = br["_do_timeline"]("frame", 120)
        assert td.time.frame == 120.0
        assert out["seconds"] == pytest.approx(119 / 60)
        out = br["_do_timeline"]("seek", -500)
        assert out["frame"] == td.time.start  # clamped, never below start
        assert br["_do_timeline"]("status")["rate"] == 60.0

    def test_unknown_action_is_reported(self, bridge):
        br, _ = bridge
        assert br["_do_timeline"]("warp")["ok"] is False


class TestTimelinePauseGuard:
    """Pausing stops frames; the main-thread pump runs on Frame Start. So a
    pause issued through the bridge disables the bridge -- including the
    `timeline play` that would undo it. It wedged a live session and cost a
    TouchDesigner restart (2026-08-26)."""

    def test_pause_is_refused_by_default(self, bridge):
        br, td = bridge
        out = br["_do_timeline"]("pause")
        assert out["ok"] is False
        assert "refusing to pause" in out["error"]
        assert td.time.play is True, "the timeline must not have been touched"

    def test_the_refusal_explains_the_consequence(self, bridge):
        br, _ = bridge
        err = br["_do_timeline"]("pause")["error"]
        assert "Frame Start" in err and "force=true" in err

    def test_the_refusal_still_reports_the_state(self, bridge):
        br, _ = bridge
        out = br["_do_timeline"]("pause")
        assert out["state"]["play"] is True and out["would_set_play"] is False

    def test_toggle_from_playing_is_refused_too(self, bridge):
        br, td = bridge
        assert br["_do_timeline"]("toggle")["ok"] is False
        assert td.time.play is True

    def test_force_pauses(self, bridge):
        br, td = bridge
        out = br["_do_timeline"]("pause", force=True)
        assert out["ok"] is True and out["play"] is False
        assert td.time.play is False

    def test_play_is_never_refused(self, bridge):
        br, td = bridge
        td.time.play = False
        out = br["_do_timeline"]("play")
        assert out["ok"] is True and td.time.play is True

    def test_toggle_into_playing_is_allowed(self, bridge):
        br, td = bridge
        td.time.play = False
        assert br["_do_timeline"]("toggle")["ok"] is True
        assert td.time.play is True


class TestPumpStallDiagnosis:
    def test_a_paused_timeline_is_named(self, bridge):
        """The HTTP thread cannot read TD, so the pump leaves the play state
        behind for the diagnosis -- and the fix is one keystroke."""
        br, td = bridge
        td.time.play = False
        br["pump"]()
        br["_last_pump"] = time.time() - 30
        msg = br["_pump_diagnosis"]()
        assert "PAUSED" in msg and "Press Play" in msg

    def test_a_playing_timeline_gets_the_general_diagnosis(self, bridge):
        br, td = bridge
        td.time.play = True
        br["pump"]()
        br["_last_pump"] = time.time() - 30
        msg = br["_pump_diagnosis"]()
        assert "modal dialog" in msg and "Restart Bridge" in msg

    def test_status_reports_the_play_state(self, bridge):
        br, td = bridge
        td.time.play = False
        br["pump"]()
        assert br["status"]()["timeline_playing"] is False

    def test_a_never_run_pump_names_the_builder(self, bridge):
        br, _ = bridge
        br["_last_pump"] = 0.0
        assert "bootstrap.py" in br["_pump_diagnosis"]()


# ---------------------------------------------------------------------------
# Second live pass, 31/34: three API-shape bugs of the family the double
# exists to catch, and could not because it had been written from the bridge.
# ---------------------------------------------------------------------------
class TestParCollectionSemantics:
    def test_a_name_is_never_in_the_collection(self):
        """ParCollection has no name-based `in`; iteration yields Par objects.
        `'opacity' in node.par` is False in TouchDesigner even when the
        parameter exists -- which is why every real set_parameters reported
        'unknown parameter: opacity (did you mean opacity?)'."""
        env = TDEnv()
        node = env.make("l1", "levelTOP", params={"opacity": 1.0})
        assert ("opacity" in node.par) is False
        assert (node.par.opacity in node.par) is True

    def test_subscript_returns_none_for_a_missing_name(self):
        env = TDEnv()
        node = env.make("l1", "levelTOP", params={"opacity": 1.0})
        assert node.par["opacity"] is not None
        assert node.par["nope"] is None

    def test_set_parameters_finds_a_real_parameter(self, bridge):
        """The live failure, end to end."""
        br, td = bridge
        node = td.make("level1", "levelTOP", params={"opacity": 1.0})
        out = br["_do_set_parameters"]("/project1/level1", {"opacity": 0.5})
        assert out["ok"] is True, out
        assert node.par.opacity.val == 0.5

    def test_import_recipe_applies_parameters_and_expressions(self, bridge):
        br, td = bridge
        td.make("dest", "containerCOMP")
        br["_do_import_recipe"]([{
            "name": "n1", "type": "noiseTOP", "params": {}, "inputs": {},
            "expressions": {"period": "absTime.seconds"}}], "/project1/dest")
        # The double's create() gives noiseTOP no parameters, so nothing to
        # assert on values -- the point is that the lookups no longer use `in`.
        assert td.project_comp.op("dest").children[0].name == "n1"


class TestChopApiShape:
    def test_chans_is_a_method(self):
        env = TDEnv()
        chop = env.make("c1", "constantCHOP", channels={"a": [1.0], "b": [2.0]})
        assert callable(chop.chans)
        assert [c.name for c in chop.chans()] == ["a", "b"]
        assert chop.chan("b").vals == [2.0]
        assert chop.chan("nope") is None
        assert chop.numChans == 2

    def test_read_chop_calls_chans(self, bridge):
        br, td = bridge
        td.make("c1", "constantCHOP", channels={"a": [1.0, 3.0]})
        out = br["_do_read_chop"]("/project1/c1")
        assert out["ok"] is True and out["channels"]["a"]["mean"] == 2.0


class TestDefaultExpressions:
    def test_a_default_expression_is_not_exported(self, bridge):
        """A container COMP ships with `resizecomp = me`. Par.isDefault covers
        value, expression and mode, so that parameter is default and stays
        out of the recipe -- it was showing up in every export."""
        br, td = bridge
        comp = td.make("box", "containerCOMP")
        p = comp.par._add(br["FakePar"]("resizecomp", "me", "me") if "FakePar" in br
                          else __import__("tests.td_double", fromlist=["FakePar"]).FakePar(
                              "resizecomp", "me", "me"))
        p.expr, p.mode = "me", FakeParMode.EXPRESSION
        p.default_expr, p.default_mode = "me", FakeParMode.EXPRESSION
        assert p.isDefault is True
        params, expressions, _, _ = br["_capture_parameters"](comp)
        assert "resizecomp" not in expressions and "resizecomp" not in params

    def test_a_user_expression_is_still_exported(self, bridge):
        br, td = bridge
        node = td.make("n1", "noiseTOP", params={"period": 1.0})
        node.par.period.expr = "absTime.seconds"
        node.par.period.mode = FakeParMode.EXPRESSION
        _, expressions, _, _ = br["_capture_parameters"](node)
        assert expressions == {"period": "absTime.seconds"}


class TestRootAndKeywordOnly:
    """Third live pass: `root` is an OP, and `clearScriptErrors` is
    keyword-only."""

    def test_root_is_the_root_comp_not_a_function(self, bridge):
        br, td = bridge
        assert br["root"] is td.root
        with pytest.raises(TypeError):
            br["root"]()

    def test_no_path_fallbacks_reach_the_root(self, bridge):
        """`_op(path) or root()` sat at nine sites, so every no-path call --
        list_nodes(), scan_network(), get_performance() -- raised
        "'td.baseCOMP' object is not callable" in TouchDesigner."""
        br, td = bridge
        td.make("n1", "noiseTOP")
        assert br["_do_list_nodes"](None)["ok"] is True
        assert br["_do_scan_network"](None, 2)["ok"] is True
        assert br["_do_get_performance"](None)["ok"] is True
        assert br["_do_timeline"]("status")["ok"] is True

    def test_clear_script_errors_is_called_by_keyword(self, bridge):
        br, td = bridge
        node = td.make("t1", "textDAT", scriptErrors="NameError: stale")
        with pytest.raises(TypeError):
            node.clearScriptErrors(False)          # positional: TD refuses
        assert br["_do_clear_script_errors"]("/project1/t1")["cleared"] == 1
