"""Wave 13 bridge behaviour, each case verified live on TouchDesigner 2025.33230
before it was modelled in the double (see UPSTREAM_SYNC_2026-11-22.md)."""

from __future__ import annotations

import pytest

from tests.td_double import FakeOP, FakeParMode, build_bridge


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
