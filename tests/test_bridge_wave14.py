"""Wave 14 bridge behaviour. Each case was reproduced on TouchDesigner
2025.33230 (TEST-01, through the HTTP tool layer) before it was modelled in
the double -- see UPSTREAM_SYNC_2026-11-26.md for the probes."""

from __future__ import annotations

import types

import pytest

from tests.td_double import FakeOP, FakeParMode, build_bridge


@pytest.fixture()
def bridge():
    return build_bridge()


def _run(br, tool, args):
    """A tool call the way the HTTP layer makes it (guards + undo + wrap)."""
    return br["_run_tool"](tool, args)


# --------------------------------------------------------------------------
# Paths: one exact operator, or nothing
# --------------------------------------------------------------------------
class TestPathArguments:
    def test_the_double_resolves_patterns_and_numbers_like_touchdesigner(self, bridge):
        br, td = bridge
        td.make("glob_alpha", "constantTOP")
        td.make("glob_beta", "constantTOP")
        # This is the trap: op() answers, with an operator nobody named.
        assert br["op"]("/project1/glob_*").name == "glob_beta"
        assert br["op"](5) is not None

    def test_wildcard_path_is_refused_before_anything_runs(self, bridge):
        br, td = bridge
        td.make("glob_alpha", "constantTOP")
        td.make("glob_beta", "constantTOP")
        out = _run(br, "delete_node", {"path": "/project1/glob_*"})
        assert out["ok"] is False and out["error_code"] == "wildcard_path"
        assert "find_nodes" in out["next_tools"]
        assert {c.name for c in td.project_comp.children} == {"glob_alpha", "glob_beta"}
        for pattern in ("/project1/glob_?eta", "/project1/[g]lob_beta"):
            assert _run(br, "get_node", {"path": pattern})["error_code"] == "wildcard_path"

    def test_non_string_path_is_refused(self, bridge):
        br, td = bridge
        td.make("n1", "noiseTOP")
        for bad in (5, {"a": 1}, ["/project1/n1"], 1.5, True):
            out = _run(br, "get_node", {"path": bad})
            assert out["ok"] is False and out["error_code"] == "bad_path", bad
        assert br["_op"](5) is None and br["_op"]("/project1/n*") is None

    def test_every_path_argument_is_checked(self, bridge):
        br, td = bridge
        td.make("a", "noiseTOP")
        td.make("b", "nullTOP")
        check = br["path_argument_error"]
        assert check("connect_nodes", {"from_path": "/project1/a", "to_path": "/project1/*"})
        assert check("diff_chop", {"path": "/project1/a", "other_path": 7})["error_code"] == "bad_path"
        assert check("copy_node", {"path": "/project1/a", "new_parent": "/proj?ct1"})
        assert check("build_network", {"spec": {}, "target_parent": "/project1/*"})
        # Legitimate: pointers, patterns in the argument made for them, no path.
        assert check("get_node", {"path": "*here"}) is None
        assert check("get_node", {"path": "*this"}) is None
        assert check("find_nodes", {"path": "/project1", "query": "noise*"}) is None
        assert check("project_info", {}) is None and check("list_nodes", {"path": None}) is None

    def test_batch_steps_are_checked_too(self, bridge):
        br, td = bridge
        td.make("glob_alpha", "constantTOP")
        out = br["_do_batch"]([{"tool": "delete_node", "args": {"path": "/project1/glob_*"}}])
        assert out["batch"][0]["result"]["error_code"] == "wildcard_path"
        assert [c.name for c in td.project_comp.children] == ["glob_alpha"]

    def test_mistyped_path_is_not_found_instead_of_the_root(self, bridge):
        br, td = bridge
        td.make("n1", "noiseTOP")
        for tool in ("list_nodes", "scan_network", "get_node", "validate_network",
                     "find_nodes", "export_recipe", "map_network"):
            out = _run(br, tool, {"path": "/project1/no_such_comp", "query": "n"})
            assert out["ok"] is False, tool
        assert _run(br, "list_nodes", {})["ok"] is True  # no path still means the root


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------
class TestWiring:
    def _two_networks(self, td):
        c1 = td.make("c1", "baseCOMP")
        c2 = td.make("c2", "baseCOMP")
        far = td.make("far_src", "noiseTOP", parent=c1)
        local = td.make("local_src", "constantTOP", parent=c2)
        dst = td.make("dst", "levelTOP", parent=c2)
        return far, local, dst

    def test_the_double_drops_the_wire_like_touchdesigner(self, bridge):
        _br, td = bridge
        far, local, dst = self._two_networks(td)
        dst.inputConnectors[0].connect(local)
        dst.inputConnectors[0].connect(far)  # no raise ...
        assert dst.inputs == []              # ... no wire, and the old one is gone

    def test_cross_network_connect_is_refused_and_the_wire_survives(self, bridge):
        br, td = bridge
        far, local, dst = self._two_networks(td)
        assert _run(br, "connect_nodes", {"from_path": local.path, "to_path": dst.path})["ok"]
        out = _run(br, "connect_nodes", {"from_path": far.path, "to_path": dst.path})
        assert out["ok"] is False and out["error_code"] == "cross_network"
        assert "selectTOP" in out["error"] and "top='/project1/c1/far_src'" in out["error"]
        assert dst.inputs == [local]

    def test_connector_index_out_of_range_is_explained(self, bridge):
        br, td = bridge
        a = td.make("a", "noiseTOP")
        b = td.make("b", "levelTOP")
        out = _run(br, "connect_nodes", {"from_path": a.path, "to_path": b.path, "to_input": 9})
        assert out["ok"] is False and "3 input connector(s)" in out["error"]
        out = _run(br, "connect_nodes", {"from_path": a.path, "to_path": b.path, "from_output": 4})
        assert out["ok"] is False and "output connector(s)" in out["error"]
        assert b.inputs == []

    def test_delete_reports_the_rewire(self, bridge):
        br, td = bridge
        m1 = td.make("m1", "noiseTOP")
        m2 = td.make("m2", "levelTOP")
        m3 = td.make("m3", "nullTOP")
        m2.inputConnectors[0].connect(m1)
        m3.inputConnectors[0].connect(m2)
        out = _run(br, "delete_node", {"path": "/project1/m2"})
        assert out["ok"] and out["deleted"] == "/project1/m2"
        assert out["rewired"] == [{"to": "/project1/m3[0]", "now_from": "/project1/m1"}]
        assert m3.inputs == [m1]
        out = _run(br, "delete_node", {"path": "/project1/m1"})
        assert out["left_unwired"] == ["/project1/m3[0]"] and "rewired" not in out
        lone = _run(br, "delete_node", {"path": "/project1/m3"})
        assert lone == {"ok": True, "deleted": "/project1/m3"}


# --------------------------------------------------------------------------
# Diagnostics TouchDesigner keeps somewhere else
# --------------------------------------------------------------------------
TRACEBACK = ('  Error: Traceback (most recent call last):\n'
             '  File "/project1/scr_callbacks", line 2, in onCook\n'
             'ValueError: boom\n'
             ' (/project1/scr_callbacks)')


class TestDiagnostics:
    def test_one_message_is_one_diagnostic(self, bridge):
        br, _td = bridge
        group = br["group_diagnostics"]
        assert group(TRACEBACK) == [TRACEBACK.rstrip()]  # was 2: the path line split off
        extension = ("  Error: NameError: name 'MyExt' is not defined \n<string>, line 1, in <module>\n"
                     "Context:(Extension 1) (/project1/extc)")
        assert len(group(extension)) == 1                  # was 3
        recursive = ("/project1/e0:  Error: Not enough sources specified (/project1/e0)\n"
                     "/project1/e1:  Error: Not enough sources specified (/project1/e1)\n"
                     "/project1/e2:  Error: Not enough sources specified (/project1/e2)")
        assert len(group(recursive)) == 3
        two = "  Error: first thing (/project1/a)\n  Error: second thing (/project1/a)"
        assert len(group(two)) == 2
        # Compiler output has no "Error:" opener and keeps the old grouping.
        assert group("ERROR: /project1/gl_pixel:3: 'x' : undeclared identifier") == \
            ["ERROR: /project1/gl_pixel:3: 'x' : undeclared identifier"]

    def test_script_operator_error_lives_on_its_docked_callbacks(self, bridge):
        br, td = bridge
        scr = td.make("scr", "scriptCHOP")
        callbacks = td.make("scr_callbacks", "textDAT", scriptErrors=TRACEBACK)
        callbacks.dock = scr
        scr.docked.append(callbacks)
        assert scr.scriptErrors() == "" and scr.errors() == ""  # the operator itself is silent
        out = br["_do_get_errors"]("/project1/scr")
        assert out["hasErrors"] and out["error_count"] == 1
        assert "ValueError: boom" in out["script_errors"][0]
        # Not counted twice when the DAT is also reached by recursion.
        assert br["_do_get_errors"]("/project1", recurse=True)["error_count"] == 1

    def test_shader_errors_come_from_compileResult_first(self, bridge):
        br, td = bridge
        gl = td.make("gl", "glslTOP", warnings="Warning: The GLSL Shader has compile errors "
                                              "(Use Info DAT to see details). (/project1/gl)")
        gl.compileResult = ("Vertex Shader Compile Results:\n\nCompiled Successfully\n\n=============\n"
                            "Pixel Shader Compile Results:\n"
                            "ERROR: /project1/gl_pixel:3: 'undefined_var' : undeclared identifier \n"
                            "ERROR: 1 compilation errors.  No code generated.\n")
        out = br["_do_get_errors"]("/project1/gl")  # no Info DAT docked at all
        assert out["hasErrors"] and any("undefined_var" in m for m in out["shader_errors"])
        assert out["warning_count"] == 1
        gl.compileResult = "Vertex Shader Compile Results:\n\nCompiled Successfully\n"
        assert br["_do_get_errors"]("/project1/gl")["shader_errors"] == []
        td.dock_info(gl, "ERROR: 0:7: 'vUV' : undeclared identifier")
        assert br["_do_get_errors"]("/project1/gl")["shader_errors"]  # the Info DAT is the fallback

    def test_recursive_read_of_a_comp_includes_shaders_inside_it(self, bridge):
        br, td = bridge
        z = td.make("z", "baseCOMP")
        gl = td.make("gl", "glslTOP", parent=z)
        gl.compileResult = ("Pixel Shader Compile Results:\n"
                            "ERROR: /project1/z/gl_pixel:3: 'q' : undeclared identifier\n")
        flat = br["_do_get_errors"]("/project1/z")
        assert flat["shader_errors"] == []  # not recursive: the COMP itself compiles nothing
        deep = br["_do_get_errors"]("/project1/z", recurse=True)
        assert deep["hasErrors"] and "'q'" in deep["shader_errors"][0]

    def test_recovery_hints_for_the_new_refusals(self, bridge):
        br, _td = bridge
        hint = br["recovery_hints"]
        assert hint("delete_node: `path`='/a/*' contains a wildcard. ...")["next_tools"] == \
            ["find_nodes", "list_nodes"]
        assert "same COMP" in hint("/a/x and /b/y are in different networks")["cause"]
        assert "returned None" in hint("'NoneType' object has no attribute 'par'")["cause"]


# --------------------------------------------------------------------------
# Parameter writes
# --------------------------------------------------------------------------
class TestParameterWrites:
    def _bound(self, td):
        holder = td.make("holder", "baseCOMP", params={"Amount": 0.0})
        xf = td.make("xf", "transformTOP", params={"tx": 0.0, "ty": 0.0, "sx": 1.0})
        return holder, xf

    def test_write_to_a_bound_parameter_lands_on_its_master(self, bridge):
        br, td = bridge
        holder, xf = self._bound(td)
        xf.par.tx.bindExpr = "op('holder').par.Amount"
        xf.par.tx.bindMaster = holder.par.Amount
        out = br["_do_set_parameters"]("/project1/xf", {"tx": 0.42})
        assert out["ok"]
        note = out["notes"]["tx"]["written_through_bind"]
        assert "/project1/holder:Amount" in note and "still bound" in note
        assert holder.par.Amount.eval() == 0.42 and xf.par.tx.mode == FakeParMode.BIND
        assert xf.par.tx.eval() == 0.42 and xf.par.tx.val == 0.0  # val is the stale constant

    def test_write_over_an_expression_keeps_its_text(self, bridge):
        br, td = bridge
        _holder, xf = self._bound(td)
        xf.par.ty.expr = "absTime.seconds"
        out = br["_do_set_parameters"]("/project1/xf", {"ty": 0.3})
        assert "EXPRESSION -> CONSTANT" in out["notes"]["ty"]["mode_changed"]
        assert "kept" in out["notes"]["ty"]["mode_changed"]
        assert xf.par.ty.expr == "absTime.seconds" and xf.par.ty.mode == FakeParMode.CONSTANT

    def test_bind_and_mode_in_the_dict_form(self, bridge):
        br, td = bridge
        _holder, xf = self._bound(td)
        out = br["_do_set_parameters"]("/project1/xf", {"tx": {"bind": "op('holder').par.Amount"},
                                                        "ty": {"expr": "me.time.frame"}})
        assert out["ok"]
        assert xf.par.tx.mode == FakeParMode.BIND and xf.par.tx.bindExpr == "op('holder').par.Amount"
        assert xf.par.ty.mode == FakeParMode.EXPRESSION
        out = br["_do_set_parameters"]("/project1/xf", {"ty": {"mode": "constant", "val": 2.0}})
        assert out["ok"] and xf.par.ty.mode == FakeParMode.CONSTANT and xf.par.ty.eval() == 2.0
        bad = br["_do_set_parameters"]("/project1/xf", {"ty": {"mode": "sideways"}})
        assert bad["ok"] is False and "mode must be" in bad["failed"]["ty"]

    def test_write_to_a_dead_bind_fails_with_the_way_out(self, bridge):
        br, td = bridge
        _holder, xf = self._bound(td)
        xf.par.sx.bindExpr = "op('gone').par.Amount"   # binds; nothing raises
        assert xf.par.sx.mode == FakeParMode.BIND and xf.par.sx.bindMaster is None
        out = br["_do_set_parameters"]("/project1/xf", {"sx": 2.0, "tx": 0.1})
        assert out["ok"] is False                      # was ok:true with "error: ..." under applied
        assert "does not resolve" in out["failed"]["sx"] and "'mode': 'constant'" in out["failed"]["sx"]
        assert out["applied"] == {"tx": 0.1} and xf.par.sx.eval() == 1.0
        fixed = br["_do_set_parameters"]("/project1/xf", {"sx": {"mode": "constant", "val": 2.0}})
        assert fixed["ok"] and xf.par.sx.eval() == 2.0

    def test_recipe_carries_binds(self, bridge, monkeypatch):
        import tests.td_double as double
        # The double creates bare operators; give the rebuilt one its parameter.
        monkeypatch.setitem(double._DEFAULT_PARS_BY_TYPE, "transformTOP", {"tx": 0.0})
        br, td = bridge
        holder, xf = self._bound(td)
        xf.par.tx.bindExpr = "op('holder').par.Amount"
        xf.par.tx.bindMaster = holder.par.Amount
        recipe = br["_do_export_recipe"]("/project1")
        spec = next(n for n in recipe["recipe"] if n["name"] == "xf")
        assert spec["expressions"]["tx"] == {"bind": "op('holder').par.Amount"}
        assert "tx" not in spec["params"]  # not the stale constant
        target = td.make("copy", "baseCOMP")
        br["_do_import_recipe"]([spec], "/project1/copy")
        rebuilt = target.op("xf")
        assert rebuilt.par.tx.mode == FakeParMode.BIND
        assert rebuilt.par.tx.bindExpr == "op('holder').par.Amount"


# --------------------------------------------------------------------------
# Sequence parameters
# --------------------------------------------------------------------------
class TestSequences:
    def _constant(self, td):
        node = td.make("c", "constantCHOP")
        node.add_sequence("const", {"name": "chan1", "value": 0.0}, 1, materialized=True)
        return node

    def _primitive(self, td):
        node = td.make("prim", "primitivePOP", params={"primtype": "polygon"})
        node.add_sequence("pt", {"posx": 0.0, "posy": 0.0, "posz": 0.0}, 1)
        return node

    def test_the_double_grows_a_sequence_on_lookup_like_touchdesigner(self, bridge):
        _br, td = bridge
        node = self._constant(td)
        assert node.par["const3bogus"] is None and node.seq[0].numBlocks == 1
        assert node.par["const4value"] is not None and node.seq[0].numBlocks == 5
        prim = self._primitive(td)
        assert [p.name for p in prim.pars("pt*")] == []  # lazily absent
        [list(b.par) for b in prim.seq[0].blocks]
        assert [p.name for p in prim.pars("pt*")] == ["pt0posx", "pt0posy", "pt0posz"]
        assert prim.seq[0].numBlocks == 1               # walking blocks adds none

    def test_read_tools_never_grow_a_sequence(self, bridge):
        br, td = bridge
        node = self._constant(td)
        out = br["_do_get_parameter_info"]("/project1/c", "const0value,const9value,bogus")
        assert set(out["parameters"]) == {"const0value"}
        assert "would add blocks" in out["missing"]["const9value"]
        assert out["sequences"] == {"const": 1} and node.seq[0].numBlocks == 1
        assert br["_do_pulse_parameter"]("/project1/c", "const7value")["ok"] is False
        assert br["_do_get_parameters"]("/project1/c", names="const*")["ok"]
        assert node.seq[0].numBlocks == 1

    def test_lazy_block_parameters_are_listed_once_the_operator_has_cooked(self, bridge):
        br, td = bridge
        prim = self._primitive(td)
        prim.totalCooks = 12  # a live operator in a running network
        out = br["_do_get_parameters"]("/project1/prim", detail="full")
        assert {"pt0posx", "pt0posy", "pt0posz"} <= set(out["params"])
        info = br["_do_get_parameter_info"]("/project1/prim")
        assert "pt0posx" in info["parameters"] and info["sequences"] == {"pt": 1}
        assert br["_do_export_recipe"]("/project1")["ok"]

    def test_reads_never_cook_an_operator_that_has_not_cooked(self, bridge):
        br, td = bridge
        prim = self._primitive(td)
        const = self._constant(td)
        for tool, args in (("get_parameters", {"path": "/project1/prim", "detailLevel": "full"}),
                           ("get_parameter_info", {"path": "/project1/prim"}),
                           ("get_parameters", {"path": "/project1/c"}),
                           ("get_parameter_info", {"path": "/project1/c", "names": "const5value"}),
                           ("export_recipe", {"path": "/project1"})):
            assert _run(br, tool, args)["ok"], tool
        # Iterating OP.seq would have cooked them (the double counts it).
        assert prim.totalCooks == 0 and not getattr(prim, "cooked_by_seq", False)
        assert const.totalCooks == 0 and not getattr(const, "cooked_by_seq", False)
        # A visible sequence is still found -- through Par.sequence, no cook.
        assert br["_do_get_parameter_info"]("/project1/c")["sequences"] == {"const": 1}
        assert const._sequences[0].numBlocks == 1
        # A hidden one is not, and that is the price of not cooking.
        assert "pt0posx" not in br["_do_get_parameters"]("/project1/prim", detail="full")["params"]

    def test_setting_a_later_block_grows_it_and_says_so(self, bridge):
        br, td = bridge
        node = self._constant(td)
        out = br["_do_set_parameters"]("/project1/c", {"const2name": "third", "const2value": 0.5})
        assert out["ok"] and out["notes"]["sequences_grown"] == {"const": "1 -> 3 blocks"}
        assert node.par["const2value"].eval() == 0.5
        again = br["_do_set_parameters"]("/project1/c", {"const0value": 1.0})
        assert "sequences_grown" not in again.get("notes", {})

    def test_runaway_growth_is_refused(self, bridge):
        br, td = bridge
        node = self._constant(td)
        out = br["_do_set_parameters"]("/project1/c", {"const400value": 1})
        assert out["ok"] is False and "const400value" in out["error"]
        assert node.seq[0].numBlocks == 1
        unknown = br["_do_set_parameters"]("/project1/c", {"const1bogus": 1})
        assert unknown["ok"] is False and "const1bogus" in unknown["unknown"]


# --------------------------------------------------------------------------
# Annotations
# --------------------------------------------------------------------------
class TestAnnotations:
    def test_create_node_assigns_the_name_touchdesigner_ignored(self, bridge):
        br, td = bridge
        raw = td.project_comp.create("annotateCOMP", "my_note")
        assert raw.name == "annotate1"  # what TouchDesigner does
        raw.destroy()
        out = br["_do_create"]("/project1", "annotateCOMP", "my_note")
        assert out["path"] == "/project1/my_note"

    def test_utility_operators_are_invisible_to_everything_else(self, bridge):
        br, td = bridge
        td.make("a1", "noiseTOP")
        note = td.project_comp.create("annotateCOMP")
        note.utility = True
        assert br["op"]("/project1/annotate1") is None
        assert [c.name for c in td.project_comp.children] == ["a1"]
        assert td.project_comp.findChildren(depth=1) == [td.project_comp.op("a1")]
        assert note in td.project_comp.findChildren(depth=1, includeUtility=True)
        listing = br["_do_list_nodes"]("/project1")
        assert [n["name"] for n in listing["nodes"]] == ["a1"] and listing["annotations"] == 1
        assert "annotate(" in listing["annotations_hint"]

    def test_create_list_update_delete(self, bridge):
        br, td = bridge
        a1 = td.make("a1", "noiseTOP")
        a2 = td.make("a2", "blurTOP")
        td.make("far", "nullTOP")
        a1.nodeX, a1.nodeY = 0, -900
        a2.nodeX, a2.nodeY = 200, -900
        td.project_comp.op("far").nodeX = 5000
        out = _run(br, "annotate", {"action": "create", "path": "/project1", "mode": "networkbox",
                                    "title": "Blur chain", "body": "noise into blur",
                                    "around": ["a1", "/project1/a2"], "name": "box_blur"})
        ann = out["annotation"]
        assert out["ok"] and ann["name"] == "box_blur" and ann["utility"] is True
        assert ann["mode"] == "networkbox" and ann["title"] == "Blur chain"
        assert ann["encloses"] == ["a1", "a2"]
        x, y, w, h = ann["rect"]
        assert x < 0 and y < -900 and x + w > 200 + 120 and y + h > -900 + 60
        listed = _run(br, "annotate", {"action": "list", "path": "/project1"})
        assert listed["count"] == 1 and listed["annotations"][0]["path"] == "/project1/box_blur"
        upd = _run(br, "annotate", {"action": "update", "path": "/project1/box_blur",
                                    "title": "Renamed", "color": [0.2, 0.4, 0.6]})
        assert upd["annotation"]["title"] == "Renamed"
        gone = _run(br, "annotate", {"action": "delete", "path": "/project1/box_blur"})
        assert gone == {"ok": True, "deleted": "/project1/box_blur"}
        assert _run(br, "annotate", {"action": "list", "path": "/project1"})["count"] == 0

    def test_refusals(self, bridge):
        br, td = bridge
        td.make("a1", "noiseTOP")
        out = _run(br, "annotate", {"action": "create", "path": "/project1", "around": ["nope"]})
        assert out["ok"] is False and "nope" in out["error"]
        assert _run(br, "annotate", {"action": "create", "path": "/project1", "mode": "sticky"})["ok"] is False
        assert td.project_comp.numChildren == 1  # the failed create left nothing behind
        assert _run(br, "annotate", {"action": "update", "path": "/project1/none"})["ok"] is False
        assert _run(br, "annotate", {"action": "explode", "path": "/project1"})["ok"] is False
        assert _run(br, "annotate", {"action": "list", "path": "/project1/a1"})["ok"] is False

    def test_rect_frames_boxes_with_a_title_bar(self, bridge):
        br, _td = bridge
        rect = br["annotation_rect"]
        assert rect([]) is None
        assert rect([(0, 0, 100, 50)], margin=10, title=20) == (-10, -10, 120, 90)
        assert rect([(0, 0, 100, 50), (300, -200, 100, 50)], margin=0, title=0) == (0, -200, 400, 250)

    def test_the_bridge_itself_cannot_be_annotated(self, bridge):
        br, td = bridge
        home = td.project_comp.add(FakeOP("td_mcp", "baseCOMP"))
        br["me"] = home.add(FakeOP("bridge", "textDAT"))
        out = _run(br, "annotate", {"action": "create", "path": "/project1/td_mcp", "title": "x"})
        assert out["ok"] is False and out["error_code"] == "self_path"


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------
def _raw(**over):
    raw = {"fps_target": 60.0, "seconds": 0.5, "frames_advanced": 30, "playing": True,
           "realtime": True, "partial": None, "errors": {}, "warnings": {}, "ops": []}
    raw.update(over)
    return raw


def _kinds(report):
    return {f["kind"]: f for f in report["findings"]}


class TestHealthReport:
    def test_a_running_clean_network_is_clean(self, bridge):
        br, _td = bridge
        out = br["health_report"](_raw(ops=[
            {"path": "/p/a", "family": "TOP", "cooks_before": 10, "cooks_after": 40, "cook_ms": 0.2},
            {"path": "/p/t", "family": "DAT", "cooks_before": 0, "cooks_after": 0, "cook_ms": 0.0}]))
        assert out["clean"] and out["findings"] == []
        assert out["summary"] == "2 operator(s) checked, 1 cooked during the check, 60/60 fps"

    def test_silent_failures_are_findings(self, bridge):
        br, _td = bridge
        ops = [
            {"path": "/p/dead", "family": "TOP", "cooks_after": 3, "dead_expressions": ["tx"]},
            {"path": "/p/bind", "family": "TOP", "cooks_after": 3, "dead_binds": ["sx"]},
            {"path": "/p/ext", "family": "COMP", "cooks_after": 1, "failed_extensions": [1]},
            {"path": "/p/off", "family": "COMP", "cooks_after": 1, "cooking_off": True},
            {"path": "/p/byp", "family": "TOP", "cooks_after": 0, "bypass": True},
            {"path": "/p/idle", "family": "CHOP", "cooks_after": 0},
            {"path": "/p/note", "family": "DAT", "cooks_after": 0},
            {"path": "/p/slow", "family": "TOP", "cooks_before": 5, "cooks_after": 9, "cook_ms": 12.0},
            {"path": "/p/old", "family": "TOP", "cooks_before": 5, "cooks_after": 5, "cook_ms": 400.0},
        ]
        out = br["health_report"](_raw(ops=ops, errors={"/p/x": ["  Error: boom (/p/x)"]},
                                       warnings={"/p/w": ["Warning: careful (/p/w)"]}))
        k = _kinds(out)
        assert k["dead-expression"]["parameters"] == {"/p/dead": ["tx"]}
        assert k["dead-bind"]["paths"] == ["/p/bind"] and k["extension-failed"]["paths"] == ["/p/ext"]
        assert k["cooking-off"]["paths"] == ["/p/off"] and k["bypassed"]["severity"] == "note"
        # Never cooked: data-flow operators only, and a bypassed one is already named.
        assert k["never-cooked"]["paths"] == ["/p/idle"]
        # Cooked before, not during: /p/old -- a note, not a fault.
        assert k["idle"]["paths"] == ["/p/old"] and k["idle"]["severity"] == "note"
        # Expensive: only what cooked during the check -- /p/old's 400 ms is history.
        assert k["expensive"]["paths"] == ["/p/slow (12.0 ms)"]
        assert "boom" in k["errors"]["message"] and k["warnings"]["count"] == 1
        assert out["clean"] is False and out["counts"]["error"] == 3
        assert [f["severity"] for f in out["findings"]] == sorted(
            (f["severity"] for f in out["findings"]), key=["error", "warning", "note"].index)

    def test_no_frames_means_no_verdict_on_cooking(self, bridge):
        br, _td = bridge
        idle = [{"path": "/p/a", "family": "TOP", "cooks_after": 0, "cook_ms": 50.0}]
        paused = br["health_report"](_raw(playing=False, frames_advanced=0, ops=idle))
        assert set(_kinds(paused)) == {"paused"} and paused["clean"]
        assert "fps" not in paused["summary"]
        stalled = br["health_report"](_raw(frames_advanced=1, ops=idle))
        k = _kinds(stalled)
        assert set(k) == {"stalled"} and k["stalled"]["severity"] == "warning"
        slow = br["health_report"](_raw(frames_advanced=12, ops=[]))
        assert _kinds(slow)["slow"]["measured_fps"] == 24.0
        offline = br["health_report"](_raw(realtime=False))
        assert "not-realtime" in _kinds(offline)

    def test_long_lists_are_capped_and_partial_is_said(self, bridge):
        br, _td = bridge
        ops = [{"path": "/p/b%02d" % i, "family": "TOP", "cooks_after": 1, "bypass": True}
               for i in range(20)]
        out = br["health_report"](_raw(ops=ops, partial="expressions read on 5 of 20 operators"))
        k = _kinds(out)
        assert k["bypassed"]["count"] == 20 and len(k["bypassed"]["paths"]) == 8 and k["bypassed"]["more"] == 12
        assert "5 of 20" in k["partial"]["message"]


class TestHealthCollection:
    def _network(self, br, td):
        z = td.make("z", "baseCOMP")
        dead = td.make("dead", "noiseTOP", parent=z, params={"tx": {
            "val": 0.0, "default": 0.0, "mode": FakeParMode.EXPRESSION, "expr": "op('nope').par.x"}})
        dead.par.tx.eval_error = RuntimeError("Error in /project1/z/dead parameter t")
        xf = td.make("xf", "transformTOP", parent=z, params={"sx": 1.0})
        xf.par.sx.bindExpr = "op('gone').par.Amount"
        byp = td.make("byp", "levelTOP", parent=z)
        byp.bypass = True
        off = td.make("sleeping", "baseCOMP", parent=z)
        off.allowCooking = False
        ext = td.make("extc", "baseCOMP", parent=z, params={"extension1": "MyExt(me)"})
        ext.extensions = [None]
        bad = td.make("bad", "transformTOP", parent=z,
                      errors="  Error: Not enough sources specified (/project1/z/bad)")
        live = td.make("live", "noiseTOP", parent=z)
        live.totalCooks = 10
        return z, live

    def test_scan_finds_each_silent_failure(self, bridge):
        br, td = bridge
        z, live = self._network(br, td)
        br["absTime"] = types.SimpleNamespace(frame=100)
        first = br["_health_first"]("/project1/z")
        live.totalCooks = 40
        live.cpuCookTime = 0.4
        br["absTime"].frame = 130
        first["t"] -= 0.5
        raw = br["_health_second"]("/project1/z", first)
        out = br["health_report"](raw)
        k = _kinds(out)
        assert k["dead-expression"]["paths"] == ["/project1/z/dead"]
        assert k["dead-bind"]["paths"] == ["/project1/z/xf"]
        assert k["bypassed"]["paths"] == ["/project1/z/byp"]
        assert k["cooking-off"]["paths"] == ["/project1/z/sleeping"]
        assert k["extension-failed"]["paths"] == ["/project1/z/extc"]
        assert k["errors"]["paths"] == ["/project1/z/bad"]
        assert "/project1/z/live" not in k["never-cooked"]["paths"]
        assert "1 cooked during the check" in out["summary"] and raw["frames_advanced"] == 30

    def test_same_frame_call_claims_nothing_about_cooking(self, bridge):
        br, td = bridge
        self._network(br, td)
        out = _run(br, "health", {"path": "/project1/z"})
        k = _kinds(out)
        assert out["ok"] and "paused" in k and "never-cooked" not in k
        assert "dead-expression" in k and out["path"] == "/project1/z"

    def test_missing_path_and_bridge_exclusion(self, bridge):
        br, td = bridge
        assert _run(br, "health", {"path": "/project1/nope"})["ok"] is False
        home = td.project_comp.add(FakeOP("td_mcp", "baseCOMP"))
        br["me"] = home.add(FakeOP("bridge", "textDAT"))
        td.make("n1", "noiseTOP")
        _start, nodes = br["_health_scope"](None)
        assert [n.path for n in nodes] == ["/project1/n1"]  # never the bridge's own operators

    def test_scan_budget_is_reported(self, bridge):
        br, td = bridge
        z, _live = self._network(br, td)
        br["HEALTH_SCAN_MS"] = -1.0
        raw = br["_health_second"]("/project1/z", br["_health_first"]("/project1/z"))
        assert "expressions read on 0 of" in raw["partial"]
        assert not any(o.get("dead_expressions") for o in raw["ops"])
        assert any(o.get("bypass") for o in raw["ops"])  # the cheap facts are still read

    def test_health_is_a_multi_frame_tool(self, bridge):
        br, _td = bridge
        assert "health" in br["_MULTI_FRAME_TOOLS"] and "health" in br["_DISPATCH"]
        assert "annotate" in br["_DISPATCH"] and br["_SELF_GUARDED"]["annotate"] == ("path", False)
