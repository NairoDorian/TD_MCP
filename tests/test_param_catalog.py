"""The live-probed parameter catalog and everything that now reads it.

Every fact asserted here was read off TouchDesigner 2025.33230 by
``scripts/probe_param_catalog_td.py``; several were first documented by other
projects the hard way (Embody v6.2.51 / v6.2.65, Derivative TDMCP #13).
"""

from __future__ import annotations

import json

import pytest

from td_mcp import validation
from td_mcp.kb import corpus, param_catalog as pc

pytestmark = pytest.mark.skipif(not pc.available(), reason="param catalog not present")


class TestCatalogShape:
    def test_every_family_probed_with_no_failures(self):
        stats = pc.stats()
        assert stats["build"] == "2025.33230"
        assert set(stats["families"]) == {"TOP", "CHOP", "SOP", "POP", "DAT", "MAT", "COMP"}
        assert stats["operators"] >= 650
        assert stats["parameters"] > 20000

    def test_names_are_python_names_not_labels(self):
        for op_type in ("levelTOP", "noiseTOP", "lfoCHOP", "gridPOP"):
            for p in pc.params(op_type):
                assert " " not in p["name"], (op_type, p["name"])
                assert p["name"] == p["name"].lower() or p["name"][:1].islower()


class TestResolveOpType:
    @pytest.mark.parametrize("spelling", ["noiseTOP", "noisetop", "Noise TOP", "noise_top"])
    def test_spellings_of_one_type(self, spelling):
        assert pc.resolve_op_type(spelling) == "noiseTOP"

    def test_display_name_with_spaces(self):
        assert pc.resolve_op_type("GLSL Multi TOP") == "glslmultiTOP"

    def test_short_type_across_families_is_ambiguous(self):
        assert pc.resolve_op_type("noise") is None
        assert set(pc.candidates("noise")) == {"noiseTOP", "noiseCHOP", "noiseSOP", "noisePOP"}
        assert pc.resolve_op_type("noise", family="CHOP") == "noiseCHOP"

    def test_unique_short_type_resolves(self):
        assert pc.resolve_op_type("lfo") == "lfoCHOP"


class TestFieldVerifiedFacts:
    """Names agents get wrong; each is a set_parameters failure avoided."""

    def _names(self, op_type):
        return {p["name"] for p in pc.params(op_type)}

    def test_blur_amount_is_size(self):
        assert "size" in self._names("blurTOP")

    def test_lag_has_two_lags(self):
        assert {"lag1", "lag2"} <= self._names("lagCHOP")

    def test_rectangle_pop_sizes(self):
        assert {"sizeu", "sizev"} <= self._names("rectanglePOP")

    def test_level_brightness_is_numbered(self):
        assert {"brightness1", "brightness2"} <= self._names("levelTOP")
        assert "brightness" not in self._names("levelTOP")

    def test_noise_monochrome_is_mono(self):
        assert "mono" in self._names("noiseTOP")

    def test_textdat_language_creation_value_differs_from_declared(self):
        lang = pc.par("textDAT", "language")
        assert lang["default"] == "text" and lang["declared_default"] == "input"

    def test_timer_chop_ships_cycle_limited(self):
        assert pc.par("timerCHOP", "cyclelimit")["default"] is True
        assert pc.par("timerCHOP", "maxcycles")["default"] == 4

    def test_execute_dat_framestart_defaults_off(self):
        assert pc.par("executeDAT", "framestart")["default"] is False

    def test_tuplets(self):
        assert pc.tuplets("noiseTOP")["resolution"] == ["resolutionw", "resolutionh"]

    def test_level_clampinput_menu(self):
        assert pc.par("levelTOP", "clampinput")["menu"] == ["automatic", "clamp", "unclamped"]


class TestDescribe:
    def test_pattern_matches_label_too(self):
        out = pc.describe("blurTOP", pattern="filter size")
        assert out["ok"] and [r["name"] for r in out["parameters"]] == ["size"]

    def test_ambiguous_type_lists_candidates(self):
        out = pc.describe("constant")
        assert not out["ok"] and "constantTOP" in out["candidates"]

    def test_unknown_type_did_you_mean(self):
        out = pc.describe("noizeTOP")
        assert not out["ok"] and "noiseTOP" in out["did_you_mean"]

    def test_offline_tool_returns_json(self):
        from td_mcp.server_offline import td_describe_op_type
        out = json.loads(td_describe_op_type("lfoCHOP", pattern="freq"))
        assert [r["name"] for r in out["parameters"]] == ["frequency"]


class TestCorpusUsesCatalog:
    def test_operator_record_accepts_op_type(self):
        rec = corpus.operator_record("levelTOP")
        assert rec is not None and "level" in rec["displayName"].lower()

    def test_param_schema_keyed_by_real_name(self):
        schema = corpus.param_schema(corpus.operator_record("Level TOP"))
        assert "brightness1" in schema and "Brightness 1" not in schema

    def test_td_docs_parameter_shows_exact_names(self):
        from td_mcp.server_offline import td_docs_parameter
        text = td_docs_parameter("levelTOP")
        assert "`brightness1`" in text and "`clampinput`" in text


class TestValidationAgainstCatalog:
    """A correctly typed spec used to fail UNKNOWN_TYPE on every operator,
    and a bad menu value passed: the pipeline judged backwards."""

    def _codes(self, spec, **kw):
        return [f["code"] for f in validation.validate_build(spec, **kw)["findings"]]

    def test_valid_op_types_pass_clean(self):
        spec = {"operators": [
            {"name": "n", "type": "noiseTOP", "parameters": {"period": 2, "mono": True}},
            {"name": "l", "type": "levelTOP", "parameters": {"brightness1": 1.2},
             "inputs": ["n"]}]}
        report = validation.validate_build(spec, strict=True)
        assert report["ok"], report["findings"]
        assert "UNKNOWN_TYPE" not in self._codes(spec)

    def test_bad_menu_value_is_an_error(self):
        spec = {"operators": [{"name": "l", "type": "levelTOP",
                               "parameters": {"clampinput": "bogus"}}]}
        assert "MENU_INVALID" in self._codes(spec)

    def test_unknown_parameter_is_an_error_with_suggestion(self):
        spec = {"operators": [{"name": "l", "type": "levelTOP", "parameters": {"zzz": 1}}]}
        msgs = [f["message"] for f in validation.validate_build(spec)["findings"]]
        assert any("no parameter 'zzz'" in m for m in msgs)

    def test_friendly_name_flagged_renamed(self):
        spec = {"operators": [{"name": "n", "type": "noiseTOP", "parameters": {"resx": 64}}]}
        assert "PARAM_RENAMED" in self._codes(spec)

    def test_ambiguous_short_type_flagged(self):
        spec = {"operators": [{"name": "c", "type": "constant"}]}
        assert "AMBIGUOUS_TYPE" in self._codes(spec)

    def test_empty_reference_parameter_flagged(self):
        spec = {"operators": [{"name": "s", "type": "selectTOP"}]}
        assert "PARAM_UNSET" in self._codes(spec)

    def test_auto_repair_fixes_names_and_menus(self):
        spec = {"operators": [
            {"name": "n", "type": "noiseTOP", "parameters": {"resx": 64, "monochrome": True}},
            {"name": "l", "type": "levelTOP", "parameters": {"clampinput": "bogus"}}]}
        fixed = validation.auto_repair(spec)
        by_name = {o["name"]: o["parameters"] for o in fixed["operators"]}
        assert by_name["n"] == {"resolutionw": 64, "mono": True}
        assert by_name["l"] == {"clampinput": "automatic"}
        assert validation.validate_build(fixed)["ok"]


class TestCatalogHoldsNoMachineState:
    """The probe records what the probing machine offers; local IPs, device
    GUIDs, open-window titles, fonts and the project path were all in the
    first raw probe. A committed catalog must be a fixed point of sanitize."""

    def test_catalog_is_already_sanitized(self):
        import copy
        dirty = []
        for op_type in pc.op_types():
            for p in pc.params(op_type):
                if pc.sanitize_par(copy.deepcopy(p)):
                    dirty.append(f"{op_type}.{p['name']}")
        assert not dirty, ("run `python -m td_mcp.kb.param_catalog --sanitize` "
                           f"after probing: {dirty[:10]}")

    def test_no_user_paths_anywhere_in_the_files(self):
        import glob
        import os
        import re
        for path in glob.glob(os.path.join(pc.CATALOG_DIR, "*.json")):
            text = open(path, encoding="utf-8").read()
            assert not re.search(r"[/\\](Users|home)[/\\]", text), path

    def test_sanitize_strips_each_kind_of_machine_state(self):
        cases = [
            {"name": "localaddress", "menu": ["10.0.0.7", "192.168.1.20"], "default": ""},
            {"name": "device", "menu": ["default", "{0.0.1.0}.{9e8181dc-b5e5}||Mic"]},
            {"name": "source", "menu": ["0", "TouchDesigner 2025.1: C:/x/p.toe"]},
            {"name": "font", "menu": ["Arial", "Verdana"], "default": "Arial"},
            {"name": "rootfolder", "default": "C:/Users/someone/project"},
        ]
        for p in cases:
            assert pc.sanitize_par(p) and p.get("machine_state") and "menu" not in p, p
        install = {"name": "file", "default":
                   "C:/Program Files/Derivative/TouchDesigner.2025.33230/Samples/Map/Banana.tif"}
        assert pc.sanitize_par(install) and install["default"] == "<td_install>/Samples/Map/Banana.tif"
        static = {"name": "clampinput", "menu": ["automatic", "clamp", "unclamped"]}
        assert not pc.sanitize_par(static) and static["menu"] == ["automatic", "clamp", "unclamped"]


class TestValidatorOutputAnAgentCanAct:
    """Found by driving td_validate_build over MCP (scripts/live_smoke.py):
    the tool returned counts only, and MENU_INVALID's hint was the generic
    "Unrecognized error from the bridge". Asserted at the tool, not at
    validate_build, because the tool is what an agent reads."""

    def test_findings_and_specific_hints_reach_the_agent(self):
        from td_mcp.server_offline import td_validate_build
        spec = {"operators": [{"name": "l", "type": "levelTOP",
                               "parameters": {"clampinput": "bogus"}}]}
        out = json.loads(td_validate_build(json.dumps(spec)))
        findings = out["validation"]["findings"]
        menu = [f for f in findings if f["code"] == "MENU_INVALID"]
        assert menu and "automatic" in menu[0]["message"] and menu[0]["target"] == "l"
        hint = [r for r in out["recovery"] if r["code"] == "MENU_INVALID"][0]["recovery"]
        assert "Unrecognized" not in hint["cause"]
        assert "td_describe_op_type" in hint["next_tools"]
