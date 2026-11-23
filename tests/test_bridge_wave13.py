"""Wave 13 bridge behaviour, each case verified live on TouchDesigner 2025.33230
before it was modelled in the double (see UPSTREAM_SYNC_2026-11-22.md)."""

from __future__ import annotations

import pytest

from tests.td_double import BRIDGE_PATH, FakeOP, FakeParMode, build_bridge


@pytest.fixture()
def bridge():
    return build_bridge()


def _install_component(br, td):
    """Load the bridge the way bootstrap.py does: a DAT inside /project1/td_mcp,
    so `me.parent()` is the bridge's home and the self-guard has something to
    fence."""
    home = td.project_comp.add(FakeOP("td_mcp", "baseCOMP"))
    dat = home.add(FakeOP("bridge", "textDAT"))
    br["me"] = dat
    return home


class TestGetParametersReadsLiveValues:
    def test_expression_mode_reports_eval_not_constant_slot(self, bridge):
        br, td = bridge
        node = td.make("n1", "noiseTOP", params={"period": {
            "val": 3.0, "default": 1.0, "mode": FakeParMode.EXPRESSION, "expr": "1.5 + 1.5"}})
        node.par.period._val = 3.0  # what eval() returns; .val would be the stale slot
        out = br["_do_get_parameters"]("/project1/n1")
        assert out["params"]["period"] == 3.0
        assert out["modes"]["period"] == {"mode": "EXPRESSION", "expr": "1.5 + 1.5"}

    def test_broken_expression_is_reported_not_raised(self, bridge):
        br, td = bridge
        node = td.make("n1", "noiseTOP", params={"amp": {
            "val": 0.5, "default": 0.5, "mode": FakeParMode.EXPRESSION, "expr": "nope + 1"}})
        node.par.amp.eval_error = RuntimeError("Error in parameter amp")
        out = br["_do_get_parameters"]("/project1/n1")
        assert out["ok"] and out["params"]["amp"].startswith("<eval error")
        assert out["modes"]["amp"]["eval_error"] is True

    def test_names_filter_and_non_default(self, bridge):
        br, td = bridge
        td.make("n1", "noiseTOP", params={"period": {"val": 2.0, "default": 1.0},
                                          "amp": {"val": 1.0, "default": 1.0},
                                          "resolutionw": {"val": 256, "default": 256}})
        assert set(br["_do_get_parameters"]("/project1/n1", names="res*")["params"]) == {"resolutionw"}
        assert set(br["_do_get_parameters"]("/project1/n1", non_default=True)["params"]) == {"period"}


class TestSetParametersReadsBack:
    def test_invalid_menu_token_fails_instead_of_silently_no_op(self, bridge):
        br, td = bridge
        td.make("l1", "levelTOP", params={"clampinput": {
            "val": "automatic", "default": "automatic", "style": "Menu",
            "menuNames": ["automatic", "clamp", "unclamped"]}})
        out = br["_do_set_parameters"]("/project1/l1", {"clampinput": "bogus"})
        assert out["ok"] is False and "valid: automatic, clamp, unclamped" in out["error"]

    def test_menu_label_maps_to_token(self, bridge):
        br, td = bridge
        node = td.make("l1", "levelTOP", params={"clampinput": {
            "val": "automatic", "default": "automatic", "style": "Menu",
            "menuNames": ["automatic", "clamp", "unclamped"]}})
        node.par.clampinput.menuLabels = ["Automatic", "Clamp [0-1]", "Unclamped"]
        out = br["_do_set_parameters"]("/project1/l1", {"clampinput": "Clamp [0-1]"})
        assert out["ok"] and node.par.clampinput.eval() == "clamp"

    def test_tuplet_name_takes_a_list(self, bridge):
        br, td = bridge
        node = td.make("n1", "noiseTOP", params={"resolutionw": {"val": 256, "default": 256, "style": "Int"},
                                                 "resolutionh": {"val": 256, "default": 256, "style": "Int"}})
        for p in (node.par.resolutionw, node.par.resolutionh):
            p._tuplet_name = "resolution"
        out = br["_do_set_parameters"]("/project1/n1", {"resolution": [1280, 720]})
        assert out["ok"], out
        assert (node.par.resolutionw.eval(), node.par.resolutionh.eval()) == (1280, 720)

    def test_writing_a_value_reports_the_discarded_expression(self, bridge):
        br, td = bridge
        node = td.make("n1", "noiseTOP", params={"period": {
            "val": 1.0, "default": 1.0, "mode": FakeParMode.EXPRESSION, "expr": "2 + 2"}})
        out = br["_do_set_parameters"]("/project1/n1", {"period": 7.0})
        assert out["ok"] and "mode_changed" in out["notes"]["period"]
        assert node.par.period.mode == FakeParMode.CONSTANT

    def test_pulse_parameter_is_pulsed_not_assigned(self, bridge):
        br, td = bridge
        node = td.make("lf1", "lfoCHOP", params={"resetpulse": {"val": False, "default": False,
                                                                "style": "Pulse"}})
        out = br["_do_set_parameters"]("/project1/lf1", {"resetpulse": True})
        assert out["ok"] and getattr(node.par.resetpulse, "pulses", 0) == 1

    def test_pulse_parameter_tool(self, bridge):
        br, td = bridge
        node = td.make("lf1", "lfoCHOP", params={"resetpulse": {"val": False, "default": False,
                                                                "style": "Pulse"},
                                                 "frequency": {"val": 1.0, "default": 1.0}})
        assert br["_do_pulse_parameter"]("/project1/lf1", "resetpulse")["ok"]
        assert node.par.resetpulse.pulses == 1
        wrong = br["_do_pulse_parameter"]("/project1/lf1", "frequency")
        assert wrong["ok"] is False and "not a pulse" in wrong["error"]
        missing = br["_do_pulse_parameter"]("/project1/lf1", "nope")
        assert missing["pulse_parameters"] == ["resetpulse"]


class TestSelfGuard:
    def test_mutations_inside_the_bridge_are_refused(self, bridge):
        br, td = bridge
        home = _install_component(br, td)
        for tool, args in (("delete_node", {"path": home.path}),
                           ("set_parameters", {"path": home.path + "/bridge", "params": {"file": ""}}),
                           ("set_dat_text", {"path": home.path + "/bridge", "text": ""}),
                           ("create_node", {"path": home.path, "type": "noiseTOP"})):
            out = br["_run_tool"](tool, args)
            assert out["ok"] is False and out["error_code"] == "self_path", tool

    def test_deleting_an_ancestor_of_the_bridge_is_refused(self, bridge):
        br, td = bridge
        _install_component(br, td)
        out = br["_run_tool"]("delete_node", {"path": "/project1"})
        assert out["error_code"] == "self_path" and "contains" in out["error"]

    def test_siblings_of_the_bridge_are_editable(self, bridge):
        br, td = bridge
        _install_component(br, td)
        td.make("n1", "noiseTOP")
        assert br["_run_tool"]("delete_node", {"path": "/project1/n1"})["ok"]

    def test_batch_is_guarded_too(self, bridge):
        br, td = bridge
        home = _install_component(br, td)
        out = br["_do_batch"]([{"tool": "delete_node", "args": {"path": home.path}}])
        assert out["batch"][0]["result"]["error_code"] == "self_path"

    def test_pathless_create_never_lands_inside_the_bridge(self, bridge):
        br, td = bridge
        home = _install_component(br, td)
        out = br["_do_create"](None, "noiseTOP")
        assert out["ok"] and not out["path"].startswith(home.path + "/")


class TestPerformance:
    def test_stale_cooks_rank_last_and_carry_no_budget(self, bridge):
        br, td = bridge
        old = td.make("old", "noiseTOP")
        live = td.make("live", "noiseTOP")
        old.cpuCookTime, old.cookAbsFrame = 9.0, 10      # cooked once, long ago
        live.cpuCookTime, live.cookAbsFrame = 1.0, 999
        br["absTime"] = type("T", (), {"frame": 1000})()
        out = br["_do_get_performance"]("/project1")
        assert out["cooks"][0]["path"] == "/project1/live"
        stale = [r for r in out["cooks"] if r["path"] == "/project1/old"][0]
        assert stale["stale"] and "budget_pct" not in stale
        assert out["stale_count"] == 1 and out["units"] == "ms"

    def test_non_comp_path_profiles_that_operator(self, bridge):
        br, td = bridge
        td.make("n1", "noiseTOP")
        out = br["_do_get_performance"]("/project1/n1")
        assert out["count"] == 1 and out["cooks"][0]["path"] == "/project1/n1"


class TestMisc:
    def test_normalize_op_type_lowercases_prefix(self, bridge):
        br, _ = bridge
        assert br["normalize_op_type"]("NoiseTOP") == "noiseTOP"
        assert br["normalize_op_type"]("glsl_multi_top") == "glslmultiTOP"

    def test_display_and_render_flags_are_settable(self, bridge):
        br, td = bridge
        node = td.make("n1", "noiseTOP")
        node.display = False
        out = br["_do_set_flags"]("/project1/n1", {"display": True})
        assert out["ok"] and node.display is True

    def test_forbidden_builtin_hint_is_about_the_sandbox(self, bridge):
        br, _ = bridge
        hint = br["recovery_hints"]("execute_python forbids the builtin(s) 'globals'. "
                                    "TouchDesigner methods like par.eval() are fine.")
        assert "blocks" in hint["cause"] and "parameter" not in hint["cause"].lower()


class TestCaptureOnTheMainThread:
    def test_non_top_in_batch_is_refused_not_saved_as_png(self, bridge):
        """Inside `batch` (main thread) frames cannot pass, so the OP Viewer
        route is unavailable; saving a CHOP itself would write a .bchan."""
        br, td = bridge
        td.make("lfo1", "lfoCHOP")
        out = br["_do_batch"]([{"tool": "capture_viewport", "args": {"path": "/project1/lfo1"}}])
        res = out["batch"][0]["result"]
        assert res["ok"] is False and res["error_code"] == "needs_frames"


class TestNoTouchDesignerAccessAtImport:
    """The bridge must not touch a TD object while its module is importing.

    1.13.3's first build read the bridge DAT's own text at module level; in
    TouchDesigner the module stopped part-way through, and every POST failed
    with "NameError: name 'MAX_BODY_BYTES' is not defined". The double could
    not see it (it has no compile-time DAT), so this checks the source: no
    module-level statement may reference a TD global, directly or by calling
    a function that does. Lambdas, defs and classes run later and are exempt.
    """

    TD_GLOBALS = {"me", "op", "parent", "root", "project", "ui", "app", "absTime",
                  "mod", "tdu", "ipar", "iop"}

    def test_module_level_code_touches_no_td_object(self):
        import ast
        tree = ast.parse(open(BRIDGE_PATH, encoding="utf-8").read())
        td = self.TD_GLOBALS

        def refs(node):
            return {n.id for n in ast.walk(node)
                    if isinstance(n, ast.Name) and n.id in td and isinstance(n.ctx, ast.Load)}

        touching = {n.name: refs(n) for n in tree.body
                    if isinstance(n, ast.FunctionDef) and refs(n)}

        class AtImport(ast.NodeVisitor):
            def __init__(self):
                self.hits = set()

            def visit_Lambda(self, node):  # runs later
                pass

            visit_FunctionDef = visit_ClassDef = visit_Lambda

            def visit_Name(self, node):
                if node.id in td and isinstance(node.ctx, ast.Load):
                    self.hits.add(node.id)

            def visit_Call(self, node):
                if isinstance(node.func, ast.Name) and node.func.id in touching:
                    self.hits.add("%s() -> %s" % (node.func.id, sorted(touching[node.func.id])))
                self.generic_visit(node)

        bad = []
        for stmt in tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
                continue
            v = AtImport()
            v.visit(stmt)
            if v.hits:
                bad.append("line %d: %s" % (stmt.lineno, sorted(v.hits)))
        assert not bad, "TD access at import time: %s" % bad


class TestPerformanceSampling:
    def test_median_hides_a_first_cook_spike_but_peak_keeps_it(self, bridge):
        br, _ = bridge
        snaps = [{"/p/noise": ("noiseTOP", 27.0, 0.2)},   # first cook after a build
                 {"/p/noise": ("noiseTOP", 0.09, 0.16)},
                 {"/p/noise": ("noiseTOP", 0.08, 0.15)}]
        out = br["perf_summary"](snaps, fps=60.0)
        row = out["cooks"][0]
        assert out["samples"] == 3 and row["cpu_ms"] == 0.09 and row["peak_ms"] == 27.2
        assert out["live_total_budget_pct"] < 2  # not the 160% a single read gave
