"""TDN fidelity (Wave 12).

Four holes, three of them found the hard way by Embody's exporter and written
up in ``github-mcp/Embody/docs/reports/tdn-exporter-fidelity-moonshine.md``:
code was silently dropped, expressions were frozen into constants, a rewire
diffed as equal, and runtime state leaked into committed files.
"""

from __future__ import annotations

import os

import pytest

from td_mcp import tdn
from tests.td_double import FakeParMode, build_bridge


# ---------------------------------------------------------------------------
# 1. A rewire is a change
# ---------------------------------------------------------------------------
class TestDiffSeesEveryField:
    def _net(self, **over):
        op = tdn.operator("a", "Null TOP", position=[0, 0], inputs=["src"],
                          parameters={"x": 1}, tags=["keep"], flags=["display"])
        op.update(over)
        return tdn.export_network(tdn.new_network(operators=[op]))

    def test_rewiring_is_not_equal(self):
        """The bug: `inputs` was never compared, in a format sold on
        diffability. A rewired network reported `is_equal: True`."""
        d = tdn.diff_tdn(self._net(), self._net(inputs=["other"]))
        assert not d["is_equal"]
        assert d["changed"][0]["field_changes"]["inputs"] == {
            "from": ["src"], "to": ["other"]}

    def test_move_is_a_change(self):
        d = tdn.diff_tdn(self._net(), self._net(position=[10, 20]))
        assert "position" in d["changed"][0]["field_changes"]

    def test_retag_is_a_change(self):
        d = tdn.diff_tdn(self._net(), self._net(tags=["other"]))
        assert "tags" in d["changed"][0]["field_changes"]

    def test_flag_change_is_a_change(self):
        d = tdn.diff_tdn(self._net(), self._net(flags=[]))
        assert "flags" in d["changed"][0]["field_changes"]

    def test_edited_shader_is_a_change(self):
        d = tdn.diff_tdn(self._net(text="void main(){}"),
                         self._net(text="void main(){ gl_FragColor=vec4(1); }"))
        assert "text" in d["changed"][0]["field_changes"]

    def test_changed_expression_is_a_change(self):
        d = tdn.diff_tdn(self._net(expressions={"x": "absTime.seconds"}),
                         self._net(expressions={"x": "absTime.frame"}))
        assert "expressions" in d["changed"][0]["field_changes"]

    def test_parameter_change_still_reported(self):
        d = tdn.diff_tdn(self._net(), self._net(parameters={"x": 2}))
        assert d["changed"][0]["param_changes"]["x"] == {"from": 1, "to": 2}

    def test_identical_network_is_equal(self):
        assert tdn.diff_tdn(self._net(), self._net())["is_equal"]


class TestIndexIsPathQualified:
    def test_same_name_under_different_parents_are_two_operators(self):
        def net(inner_type):
            return tdn.export_network(tdn.new_network(operators=[
                tdn.operator("a", "Container COMP", children=[
                    tdn.operator("noise1", "Noise TOP")]),
                tdn.operator("b", "Container COMP", children=[
                    tdn.operator("noise1", inner_type)]),
            ]))

        d = tdn.diff_tdn(net("Noise TOP"), net("Level TOP"))
        assert len(d["changed"]) == 1
        assert d["changed"][0]["path"] == "/b/noise1"


# ---------------------------------------------------------------------------
# 2. Runtime state stays out of committed files -- visibly
# ---------------------------------------------------------------------------
class TestExclusions:
    def test_tag_names_the_excluded_parameter(self):
        assert tdn.excluded_parameters(["tdn_exclude:Port", "other"]) == ["Port"]

    def test_bare_prefix_with_no_name_is_ignored(self):
        assert tdn.excluded_parameters(["tdn_exclude:", "tdn_exclude:  "]) == []

    def test_excluded_value_is_dropped_and_recorded(self):
        """A silently omitted parameter is indistinguishable from a default
        one, so the omission has to be visible in the file."""
        op = tdn.operator("out", "Moonbeam TOP", tags=["tdn_exclude:Port"],
                          parameters={"Port": 17361, "Active": 1})
        cleaned = tdn.apply_exclusions(op)
        assert "Port" not in cleaned["parameters"]
        assert cleaned["parameters"]["Active"] == 1
        assert cleaned["excluded_parameters"] == ["Port"]

    def test_a_typo_is_reported_not_silently_honoured(self):
        """The worst outcome is the leak staying while the author believes it
        is fixed."""
        op = tdn.operator("out", "Moonbeam TOP", tags=["tdn_exclude:Prot"],
                          parameters={"Port": 17361})
        cleaned = tdn.apply_exclusions(op)
        assert cleaned["excluded_parameters_unknown"] == ["Prot"]
        assert cleaned["parameters"]["Port"] == 17361

    def test_expressions_survive_exclusion(self):
        """An expression is configuration; the constant it evaluates to is the
        session state worth losing."""
        op = tdn.operator("out", "Null TOP", tags=["tdn_exclude:w"],
                          parameters={"w": 1280},
                          expressions={"w": "me.par.res"})
        cleaned = tdn.apply_exclusions(op)
        assert "w" not in cleaned["parameters"]
        assert cleaned["expressions"]["w"] == "me.par.res"

    def test_export_applies_exclusions_recursively(self):
        net = tdn.new_network(operators=[
            tdn.operator("box", "Container COMP", children=[
                tdn.operator("deck", "Movie File In TOP",
                             tags=["tdn_exclude:file"],
                             parameters={"file": "C:/Users/someone/a.png"})])])
        text = tdn.export_network(net)
        assert "C:/Users/someone" not in text
        assert "excluded_parameters" in text

    def test_untagged_operator_is_untouched(self):
        op = tdn.operator("a", "Null TOP", parameters={"x": 1})
        assert tdn.apply_exclusions(op) is op


# ---------------------------------------------------------------------------
# 3. Any doubt embeds
# ---------------------------------------------------------------------------
class TestDatTextPolicy:
    def test_unbacked_code_is_always_embedded(self):
        """The export is the only copy; dropping it loses the code with the
        network."""
        assert tdn.dat_text_policy("def onCook(): pass") == (True, "embedded")

    def test_file_backed_content_is_not_duplicated(self):
        assert tdn.dat_text_policy("x", file_backed=True) == (False, "file")

    def test_externalized_content_is_not_duplicated(self):
        assert tdn.dat_text_policy("x", externalized=True) == (False, "file")

    def test_empty_text_is_neither(self):
        assert tdn.dat_text_policy("") == (False, "empty")

    def test_attach_records_the_source(self):
        op = tdn.attach_dat_text(tdn.operator("d", "Text DAT"), "code")
        assert op["text"] == "code" and op["text_source"] == "embedded"
        op2 = tdn.attach_dat_text(tdn.operator("d", "Text DAT"), "code",
                                  file_backed=True)
        assert "text" not in op2 and op2["text_source"] == "file"

    def test_text_survives_a_roundtrip(self):
        net = tdn.new_network(operators=[
            tdn.operator("glsl1_pixel", "Text DAT", text="void main(){}")])
        back = tdn.import_network(tdn.export_network(net))
        assert back["operators"][0]["text"] == "void main(){}"


# ---------------------------------------------------------------------------
# 4. A relative output path escapes the project
# ---------------------------------------------------------------------------
class TestOutputPaths:
    def test_relative_path_anchors_to_the_project_not_the_cwd(self):
        out = tdn.resolve_output_path("touch/moonshine.tdn", "/repo/moonshine")
        assert out == os.path.normpath("/repo/moonshine/touch/moonshine.tdn")

    def test_absolute_path_is_kept(self):
        p = os.path.abspath("/tmp/x.tdn")
        assert tdn.resolve_output_path(p, "/repo") == os.path.normpath(p)

    def test_empty_path_is_refused(self):
        with pytest.raises(ValueError):
            tdn.resolve_output_path("", "/repo")

    def test_relative_to_project_round_trips(self):
        out = tdn.resolve_output_path("a/b.tdn", os.path.abspath("/repo"))
        assert tdn.relative_to_project(out, os.path.abspath("/repo")) == "a/b.tdn"

    def test_outside_the_project_is_none_not_an_exception(self):
        """`relative_to` raising into a broad except is how the original bug
        completed 'successfully' while tracking nothing."""
        assert tdn.relative_to_project(os.path.abspath("/elsewhere/x.tdn"),
                                       os.path.abspath("/repo")) is None


# ---------------------------------------------------------------------------
# The live half: recipes must carry code and wiring
# ---------------------------------------------------------------------------
@pytest.fixture()
def bridge():
    return build_bridge()


class TestRecipeFidelity:
    def test_expressions_are_captured_not_frozen(self, bridge):
        br, td = bridge
        node = td.make("n1", "noiseTOP", params={"period": 1.0})
        node.par.period.expr = "absTime.seconds"
        node.par.period.mode = FakeParMode.EXPRESSION
        spec = br["_do_export_recipe"]("/project1", 2)["recipe"]
        n1 = [s for s in spec if s["name"] == "n1"][0]
        assert n1["expressions"] == {"period": "absTime.seconds"}
        assert "period" not in n1["params"]

    def test_authored_dat_text_is_captured(self, bridge):
        br, td = bridge
        td.make("t1", "textDAT")
        td.project_comp.children[-1].text = "def onCook(dat): pass"
        spec = br["_do_export_recipe"]("/project1", 2)["recipe"]
        t1 = [s for s in spec if s["name"] == "t1"][0]
        assert t1["text"] == "def onCook(dat): pass"
        assert t1["text_source"] == "embedded"

    def test_file_backed_dat_text_is_not_duplicated(self, bridge):
        br, td = bridge
        node = td.make("t1", "textDAT", params={"file": "scripts/a.py"})
        node.text = "loaded from disk"
        spec = br["_do_export_recipe"]("/project1", 2)["recipe"]
        t1 = [s for s in spec if s["name"] == "t1"][0]
        assert "text" not in t1 and t1["text_source"] == "file"

    def test_excluded_parameter_is_dropped_and_reported(self, bridge):
        br, td = bridge
        node = td.make("out1", "nullTOP", params={"port": 17361})
        node.par.port.val = 17361
        node.tags = {"tdn_exclude:port"}
        spec = br["_do_export_recipe"]("/project1", 2)["recipe"]
        out1 = [s for s in spec if s["name"] == "out1"][0]
        assert "port" not in out1["params"]
        assert out1["excluded_parameters"] == ["port"]

    def test_a_typoed_exclusion_warns(self, bridge):
        br, td = bridge
        node = td.make("out1", "nullTOP", params={"port": 17361})
        node.tags = {"tdn_exclude:prot"}
        out = br["_do_export_recipe"]("/project1", 2)
        assert any("no such parameter" in w for w in out["warnings"])

    def test_recipe_roundtrip_rewires(self, bridge):
        """Wiring was exported as the source operator's PATH and imported by
        NAME, so no wire was ever reconnected and nothing said so."""
        br, td = bridge
        src = td.make("noise1", "noiseTOP")
        dst = td.make("level1", "levelTOP")
        dst.inputConnectors[0].connect(src)

        recipe = br["_do_export_recipe"]("/project1", 2)["recipe"]
        level = [s for s in recipe if s["name"] == "level1"][0]
        assert level["inputs"] == {"0": "noise1"}

        target = td.make("dest", "containerCOMP")
        out = br["_do_import_recipe"]([s for s in recipe
                                       if s["name"] in ("noise1", "level1")],
                                      "/project1/dest")
        assert out["wired"] == 1, out
        assert out.get("unwired") is None
        rebuilt = [c for c in target.children if c.name == "level1"][0]
        # Connector_Class: `connections` holds the far-end *connectors*, and
        # the operator is `connector.owner`. There is no `outputOP`.
        assert rebuilt.inputConnectors[0].connections[0].owner.name == "noise1"

    def test_legacy_path_style_inputs_still_wire(self, bridge):
        """Recipes exported before the fix recorded full paths."""
        br, td = bridge
        td.make("dest", "containerCOMP")
        out = br["_do_import_recipe"]([
            {"name": "noise1", "type": "noiseTOP", "params": {}, "inputs": {}},
            {"name": "level1", "type": "levelTOP", "params": {},
             "inputs": {"0": "/project1/noise1"}},
        ], "/project1/dest")
        assert out["wired"] == 1

    def test_an_unresolvable_source_is_reported(self, bridge):
        br, td = bridge
        td.make("dest", "containerCOMP")
        out = br["_do_import_recipe"]([
            {"name": "level1", "type": "levelTOP", "params": {},
             "inputs": {"0": "missing"}}], "/project1/dest")
        assert out["wired"] == 0
        assert out["unwired"] == ["level1[0] <- missing"]

    def test_imported_expressions_are_restored(self, bridge):
        br, td = bridge
        target = td.make("dest", "containerCOMP")
        br["_do_import_recipe"]([{
            "name": "n1", "type": "noiseTOP", "params": {},
            "inputs": {}, "expressions": {"period": "absTime.seconds"}}],
            "/project1/dest")
        # The double creates operators with no parameters, so restoring an
        # expression onto an absent parameter must not raise.
        assert [c.name for c in target.children] == ["n1"]

    def test_imported_dat_text_is_restored(self, bridge):
        br, td = bridge
        target = td.make("dest", "containerCOMP")
        br["_do_import_recipe"]([{
            "name": "t1", "type": "textDAT", "params": {}, "inputs": {},
            "text": "def onCook(dat): pass"}], "/project1/dest")
        assert target.children[0].text == "def onCook(dat): pass"


# ---------------------------------------------------------------------------
# COMP.create(opType, name) -- type first
# ---------------------------------------------------------------------------
class TestOpTypeNormalization:
    def test_display_names_become_op_types(self, bridge):
        br, _ = bridge
        n = br["normalize_op_type"]
        assert n("Noise TOP") == "noiseTOP"
        assert n("Movie File In TOP") == "moviefileinTOP"
        assert n("GLSL Multi TOP") == "glslmultiTOP"
        assert n("Ambient Light COMP") == "ambientlightCOMP"

    def test_camel_types_are_left_alone(self, bridge):
        br, _ = bridge
        assert br["normalize_op_type"]("noiseTOP") == "noiseTOP"

    def test_family_suffix_case_is_restored(self, bridge):
        br, _ = bridge
        assert br["normalize_op_type"]("noisetop") == "noiseTOP"

    def test_unknown_strings_pass_through(self, bridge):
        br, _ = bridge
        assert br["normalize_op_type"]("notAnOperator") == "notAnOperator"

    def test_create_passes_the_type_first(self, bridge):
        """The double refuses an invalid opType, so a reversed call raises."""
        br, td = bridge
        out = br["_do_create"]("/project1", "Noise TOP", "myNoise")
        assert out["ok"] is True
        node = td.project_comp.children[-1]
        assert node.name == "myNoise" and node.OPType == "noiseTOP"

    def test_create_without_a_name_lets_td_autoname(self, bridge):
        br, td = bridge
        br["_do_create"]("/project1", "noiseTOP")
        assert td.project_comp.children[-1].name == "noise1"
