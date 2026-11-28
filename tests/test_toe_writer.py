"""The offline .toe/.tox editor (`td_mcp.toe_writer`).

The byte-level expectations below are what TouchDesigner 2025.33230 wrote
when the same state was set in TouchDesigner and saved (UPSTREAM_SYNC_2026-11-28.md).
Edits run on an expanded fixture tree, so the suite needs neither
TouchDesigner nor toecollapse; the round trip through the real tools is
tested when they are installed, and in TouchDesigner by
``scripts/live_toe_edit.py``.
"""

import json
import struct

import pytest

from td_mcp import server_offline as so
from td_mcp import toe_reader as tr
from td_mcp import toe_writer as tw
from td_mcp.kb import param_catalog
from tests import test_toe_reader as fx

needs_catalog = pytest.mark.skipif(not param_catalog.available(), reason="parameter catalog not built")
needs_tools = pytest.mark.skipif(not (tr.find_toeexpand() and tw.find_toecollapse()),
                                 reason="TouchDesigner's toeexpand/toecollapse are not installed")


def expanded(tmp_path):
    """The reader's fixture component as toeexpand leaves it: tree + `.toc`."""
    root = fx.write_tree(tmp_path / "demo.tox.dir")
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    files.remove(".build")
    (tmp_path / "demo.tox.toc").write_bytes(
        ("\n".join([tw.TOX_TOC_HEADER, ".build"] + files) + "\n").encode("utf-8"))
    return root


@pytest.fixture
def ws(tmp_path):
    w = tw.Workspace(str(expanded(tmp_path)), root=str(tmp_path / "work"))
    yield w
    w.cleanup()


def parm_lines(ws, path):
    return (ws.dir / (path.strip("/") + ".parm")).read_text(encoding="utf-8").split("\n")


# --------------------------------------------------------------------------
# Writing what TouchDesigner writes
# --------------------------------------------------------------------------
def test_fields_are_written_the_way_touchdesigner_saves_them():
    assert tw.format_field("plain") == "plain"
    assert tw.format_field("two words") == '"two words"'
    assert tw.format_field("") == '""'
    assert tw.format_field('quote"inside') == 'quote\\"inside'          # escaped, not quoted
    assert tw.format_field("back\\slash") == "back\\\\slash"
    assert tw.format_field("ends with backslash\\") == '"ends with backslash\\\\"'
    assert tw.format_field("line1\nline2") == '"line1\nline2"'
    assert tw.format_field("caf\u00e9") == "\ufeffcaf\u00e9"            # non-ASCII: BOM first
    assert tw.format_field("two w\u00f6rds") == '\ufeff"two w\u00f6rds"'
    assert tw.format_field("x\r\ny") == '"x\r\ny"'


@pytest.mark.parametrize("line", [
    "Fset 67108928 0.75",
    "Fexpr 67108945 0 absTime.seconds",
    "Fbind 67109443 0 op('../noise1').par.period",
    "amp 16 0.25 me.time.seconds",
    'Sexpr1 67108945 "" "\'two words\'"',
    'Sexpr2 67108945 "" op(\\"noise1\\").name',
    'S10 67108928 \ufeff"uni \u00e9\u4e2d\u6587 \u2014 dash"',
    "U4 67108928 \ufeff\u00e9\\\"q",
    'S25 67108928 "x\r\ny"',
    'S20 67108928 "\\"fully quoted\\""',
    "S18 67108928 a\\\\\\\\b",
    "Pivotdistance 471859779 0 \"(me, 'PivotDistance')\" \"(me, 'PivotDistance')\"",
])
def test_parameter_lines_round_trip_exactly(line):
    rec = tr.parse_parm_line(line)
    assert tw.format_parm(rec) == line


def test_reader_unescapes_bare_fields():
    assert tr.parse_parm_line("S2 67108928 quote\\\"inside")["value"] == 'quote"inside'
    assert tr.parse_parm_line("S3 67108928 back\\\\slash")["value"] == "back\\slash"
    assert tr.parse_parm_line('Sexpr2 67108945 "" op(\\"noise1\\").name')["expr"] == 'op("noise1").name'


def test_payloads_match_saved_bytes():
    # A Text DAT holding "def f():\n\treturn 'cafe'" as saved by 2025.33230.
    body = "def f():\n\treturn 'caf\u00e9'\n"
    data = tw.text_payload(body)
    assert data[:27] == b"2\n*" + struct.pack(">6I", 1, 1, 1, 1, 2, 25)
    assert tr.read_text_payload(data) == body
    assert tw.text_payload("") == b"2\n*" + struct.pack(">6I", 1, 1, 1, 1, 2, 0)
    rows = [["name", "value", ""], ["a b", "1.5", "caf\u00e9"]]
    assert tr.read_table_payload(tw.table_payload(rows)) == rows
    assert tw.table_payload([]) == b"1\n*" + struct.pack(">4I", 1, 0, 0, 0)
    assert tw.table_payload([["a"], ["b", "c"]])[3:19] == struct.pack(">4I", 1, 2, 2, 0)  # padded


def test_untouched_records_keep_their_bytes(tmp_path):
    f = tmp_path / "x.parm"
    text = "?\nweird 0   two   spaces\nlabel 16 trails me.name\n?\n"
    f.write_bytes(text.encode("utf-8"))
    pf = tw.ParmFile(f)
    assert pf.render() == text
    rec = dict(pf.get("label"))
    rec["value"] = "new"
    pf.put(rec)
    assert pf.render() == "?\nweird 0   two   spaces\nlabel 16 new me.name\n?\n"


def test_node_file_edits_keep_unknown_statements():
    nf = tw.NodeFile(text="TOP:noise\nv 1 2 3\ntile 0 0 130 90\nflags =  viewer 1 parlanguage 0\n"
                          "color 0.67 0.67 0.67 \nview\n{\nkeep me\n}\nend\n")
    nf.set_flag("bypass", "on")
    nf.set_flag("viewer", None)
    nf.set_comment("two\nlines")
    nf.set_inputs([(1, "b"), (0, "a")])
    nf.set_tile(10, 20)
    out = nf.render()
    assert "flags =  bypass on parlanguage 0" in out
    assert 'comment "two\nlines"\ntile 10 20 130 90' in out
    assert "inputs\n{\n0 \ta\n1 \tb\n}\ncolor" in out
    assert "view\n{\nkeep me\n}\nend" in out
    assert tr.parse_node(out)["comment"] == "two\nlines"


# --------------------------------------------------------------------------
# Edits
# --------------------------------------------------------------------------
@needs_catalog
def test_set_params_constant_expression_bind_tuplet(ws):
    tw.apply(ws, [{"op": "set_params", "path": "/demo/xf", "params": {
        "ty": 0.5,                                  # over an expression: text kept, mode constant
        "tx": {"expr": "absTime.seconds"},
        "rotate": {"mode": "constant", "val": 45},  # unbind
        "s": [3, 4],                                # tuplet
        "extend": "mirror",
    }}])
    recs = {r["name"]: r for r in tr.parse_parms((ws.dir / "demo/xf.parm").read_text(encoding="utf-8"))}
    assert recs["ty"]["mode"] == "constant" and recs["ty"]["value"] == "0.5"
    assert recs["ty"]["expr"] == "absTime.seconds * 0.1"
    assert recs["tx"]["mode"] == "expression" and recs["tx"]["expr"] == "absTime.seconds"
    assert recs["rotate"]["mode"] == "constant" and recs["rotate"]["value"] == "45"
    assert (recs["sx"]["value"], recs["sy"]["value"]) == ("3", "4")
    assert recs["extend"]["value"] == "mirror"


@needs_catalog
def test_new_records_get_the_flags_touchdesigner_writes(ws):
    tw.apply(ws, [{"op": "set_params", "path": "/demo/noise1", "params": {"amp": 0.25}},
                  {"op": "set_params", "path": "/demo/holder", "params": {"Kind": "two", "Pos": [1, 2, 3]}}])
    noise = {r["name"]: r for r in tr.parse_parms((ws.dir / "demo/noise1.parm").read_text(encoding="utf-8"))}
    assert noise["amp"]["flags"] == 0
    holder = {r["name"]: r for r in tr.parse_parms((ws.dir / "demo/holder.parm").read_text(encoding="utf-8"))}
    assert holder["Kind"]["flags"] == tw.CUSTOM_BASE_FLAGS and holder["Kind"]["value"] == "two"
    assert [holder[n]["value"] for n in ("Posx", "Posy", "Posz")] == ["1", "2", "3"]


@needs_catalog
def test_set_params_refuses_what_touchdesigner_would_ignore(ws):
    with pytest.raises(tw.ToeEditError) as e:
        tw.apply(ws, [{"op": "set_params", "path": "/demo/noise1", "params": {"perod": 2}}])
    assert "period" in e.value.extra["did_you_mean"]
    with pytest.raises(tw.ToeEditError) as e:
        tw.apply(ws, [{"op": "set_params", "path": "/demo/noise1", "params": {"type": "nosuch"}}])
    assert "simplex3d" in e.value.extra["valid"]
    with pytest.raises(tw.ToeEditError):
        tw.apply(ws, [{"op": "set_params", "path": "/demo/noise1", "params": {"mono": "maybe"}}])
    with pytest.raises(tw.ToeEditError):
        tw.apply(ws, [{"op": "set_params", "path": "/demo/noise1", "params": {"period": "fast"}}])
    with pytest.raises(tw.ToeEditError):
        tw.apply(ws, [{"op": "set_params", "path": "/demo/noise*", "params": {"period": 2}}])
    # A menu index picks the token.
    tw.apply(ws, [{"op": "set_params", "path": "/demo/noise1", "params": {"type": 0}}])
    assert "type 0 perlin2d" in parm_lines(ws, "/demo/noise1")


@needs_catalog
def test_default_removes_the_line_and_sequences_report_growth(ws):
    res = tw.apply(ws, [{"op": "set_params", "path": "/demo/noise1", "params": {"period": None}},
                        {"op": "set_params", "path": "/demo/const1", "params": {"const3name": "late"}}])
    assert not any(line.startswith("period ") for line in parm_lines(ws, "/demo/noise1"))
    grown = res[1]["sequences_grown"]["const"]
    assert grown == {"from": 2, "to": 4}
    assert "const2name" in res[1]["notes"][0]  # the skipped block repeats a default name


@needs_catalog
def test_dat_text_and_tables(ws):
    tw.apply(ws, [{"op": "set_text", "path": "/demo/txt", "text": "new\r\ncode\n"},
                  {"op": "replace_text", "path": "/demo/txt", "old": "code", "new": "text"},
                  {"op": "set_cells", "path": "/demo/t32", "cells": [{"row": "ccc", "col": 1, "value": 7}]}])
    assert tr.read_text_payload((ws.dir / "demo/txt.text").read_bytes()) == "new\ntext\n"
    table = tr.read_table_payload((ws.dir / "demo/t32.table").read_bytes())
    assert table[1] == ["ccc", "7"]
    with pytest.raises(tw.ToeEditError):
        tw.apply(ws, [{"op": "set_text", "path": "/demo/noise1", "text": "x"}])
    with pytest.raises(tw.ToeEditError):
        tw.apply(ws, [{"op": "replace_text", "path": "/demo/txt", "old": "absent", "new": "x"}])


@needs_catalog
def test_wiring_rules(ws):
    res = tw.apply(ws, [{"op": "connect", "from": "/demo/noise1", "to": "/demo/comp1", "input": 1}])
    assert res[0]["replaced"] == "/demo/xf"
    assert tr.parse_node((ws.dir / "demo/comp1.n").read_text(encoding="utf-8"))["inputs"] == \
        [(0, "noise1"), (1, "noise1")]
    for bad in ({"from": "/demo/noise1", "to": "/demo/inner/out1"},      # another network
                {"from": "/demo/const1", "to": "/demo/comp1"},           # CHOP into TOP
                {"from": "/demo/noise1", "to": "/demo/xf", "input": 3}):  # Transform TOP has 1 input
        with pytest.raises(tw.ToeEditError):
            tw.apply(ws, [dict(bad, op="connect")])
    res = tw.apply(ws, [{"op": "disconnect", "to": "/demo/comp1", "input": 1}])
    assert res[0]["removed"] == {"1": "/demo/noise1"}


@needs_catalog
def test_rename_follows_wires_and_reports_text_mentions(ws):
    tw.apply(ws, [{"op": "set_params", "path": "/demo/noise1", "params": {"tx": {"expr": "op('xf').par.tx"}}}])
    res = tw.apply(ws, [{"op": "rename", "path": "/demo/xf", "name": "xform"}])[0]
    assert res["new_path"] == "/demo/xform" and "/demo/comp1" in res["rewired"]
    assert (ws.dir / "demo/xform.parm").is_file() and not (ws.dir / "demo/xf.n").exists()
    assert "demo/xform.n" in ws.toc and "demo/xf.n" not in ws.toc
    # The COMP fed by xf through its .network follows too.
    assert "0 \txform" in (ws.dir / "demo/inner.network").read_text(encoding="utf-8")
    assert any(m["path"] == "/demo/noise1" for m in res["still_mentions_old_name"])


@needs_catalog
def test_delete_add_build(ws):
    res = tw.apply(ws, [{"op": "delete", "path": "/demo/xf"}])[0]
    assert "/demo/comp1" in res["left_unwired"]
    assert not any(t.startswith("demo/xf.") for t in ws.toc)
    res = tw.apply(ws, [
        {"op": "add", "parent": "/demo", "name": "geo2", "type": "geometryCOMP"},
        {"op": "build", "parent": "/demo/geo2", "spec": {"operators": [
            {"name": "s", "type": "sphereSOP", "flags": {"display": True, "render": True}}]}},
        {"op": "add", "parent": "/demo", "name": "lv", "type": "levelTOP", "inputs": ["noise1"],
         "params": {"opacity": 0.5}},
        {"op": "add", "parent": "/demo", "name": "code2", "type": "textDAT", "text": "x = 1\n"},
    ])
    geo = tr.parse_node((ws.dir / "demo/geo2.n").read_text(encoding="utf-8"))
    assert geo["flags"].get("render") is True and geo["flags"].get("display") is True
    assert "torus" in res[0]["notes"][0]
    assert ws.toc.index("demo/code2.n") < ws.toc.index("demo/code2.text")
    assert tr.read_text_payload((ws.dir / "demo/code2.text").read_bytes()) == "x = 1\n"
    project = tr.Project(str(ws.dir), source="demo.tox")
    assert project.node("/demo/lv").op_type == "levelTOP"
    assert project.inputs_of(project.node("/demo/lv")) == [{"index": 0, "from": "/demo/noise1"}]
    with pytest.raises(tw.ToeEditError):
        tw.apply(ws, [{"op": "add", "parent": "/demo", "name": "lv", "type": "levelTOP"}])  # exists
    with pytest.raises(tw.ToeEditError):
        tw.apply(ws, [{"op": "add", "parent": "/demo", "name": "b", "type": "buttonCOMP"}])  # built by TD
    with pytest.raises(tw.ToeEditError):
        tw.apply(ws, [{"op": "add", "parent": "/demo", "name": "z", "type": "noSuchTOP"}])


@needs_catalog
def test_flags_and_node_attributes(ws):
    tw.apply(ws, [{"op": "set_flags", "path": "/demo/xf", "flags": {"bypass": False}},
                  {"op": "set_flags", "path": "/demo/inner", "flags": {"cooking": False}},
                  {"op": "set_node", "path": "/demo/noise1", "comment": 'say "hi"', "tags": ["a b"],
                   "position": [5, 6], "color": [0.1, 0.2, 0.3]}])
    assert tr.parse_node((ws.dir / "demo/xf.n").read_text(encoding="utf-8"))["flags"].get("bypass") is None
    assert tr.parse_node((ws.dir / "demo/inner.n").read_text(encoding="utf-8"))["flags"]["cook"] is False
    n = tr.parse_node((ws.dir / "demo/noise1.n").read_text(encoding="utf-8"))
    assert n["comment"] == 'say "hi"' and n["tags"] == ["a b"] and (n["x"], n["y"]) == (5, 6)
    for flags in ({"lock": True}, {"cooking": False}, {"glow": True}):
        with pytest.raises(tw.ToeEditError):
            tw.apply(ws, [{"op": "set_flags", "path": "/demo/noise1", "flags": flags}])


@needs_catalog
def test_replace_and_relink(ws):
    tw.apply(ws, [{"op": "set_params", "path": "/demo/noise1", "params": {"font": "C:/assets/a.ttf"}, "force": True}])
    res = tw.apply(ws, [{"op": "replace", "pattern": "absTime", "replacement": "me.time", "scope": "expressions"}])[0]
    assert res["replaced"] == 1 and "/demo/xf" in res["per_operator"]
    with pytest.raises(tw.ToeEditError):
        tw.apply(ws, [{"op": "replace", "pattern": "nowhere", "replacement": "x"}])
    res = tw.apply(ws, [{"op": "relink", "from": "C:/assets", "to": "D:/media", "any_parameter": True}])[0]
    assert res["parameters"][0]["to"] == "D:/media/a.ttf"


def test_tree_check_catches_an_unlisted_file(ws):
    (ws.dir / "demo" / "stray.n").write_text("TOP:null\nend\n", encoding="utf-8")
    with pytest.raises(tw.ToeEditError) as e:
        ws.check_tree()
    assert "demo/stray.n" in e.value.extra["present_but_unlisted"]


@needs_catalog
def test_edit_is_all_or_nothing(tmp_path):
    src = expanded(tmp_path)
    out = tmp_path / "out.tox"
    res = tw.edit(str(src), [{"op": "set_params", "path": "/demo/noise1", "params": {"period": 3}},
                             {"op": "set_params", "path": "/demo/noise1", "params": {"nope": 1}}], out=str(out))
    assert res["ok"] is False and res["failed_edit"] == 1 and res["nothing_written"]
    assert not out.exists()
    out.write_bytes(b"x")
    res = tw.edit(str(src), [{"op": "set_params", "path": "/demo/noise1", "params": {"period": 3}}], out=str(out))
    assert res["ok"] is False and "never overwrites" in res["error"]
    res = tw.edit(str(src), [{"op": "explode"}], out=str(tmp_path / "o2.tox"))
    assert "set_params" in res["error"]


def test_writer_selftest_on_the_fixture(tmp_path):
    stats = tw.selftest(str(expanded(tmp_path).parent))["stats"]
    assert stats.get("parm_records_different", 0) == 0 and stats["parm_records_identical"] > 10
    assert stats.get("text_different", 0) == 0 and stats.get("table_different", 0) == 0


def test_offline_tool_wrappers(tmp_path):
    src = expanded(tmp_path)
    out = json.loads(so.td_project_edit(str(src), '[{"op": "nope"}]', out=str(tmp_path / "x.tox")))
    assert out["ok"] is False and out["nothing_written"]
    out = json.loads(so.td_project_edit(str(src), "not json"))
    assert out["ok"] is False


# --------------------------------------------------------------------------
# Through TouchDesigner's own tools, when installed
# --------------------------------------------------------------------------
@needs_tools
@needs_catalog
def test_new_component_round_trips_through_toecollapse(tmp_path, monkeypatch):
    monkeypatch.setenv("TD_MCP_HOME", str(tmp_path / "home"))
    out = tmp_path / "fresh.tox"
    res = tw.edit(None, [{"op": "build", "parent": "/fresh", "spec": {"operators": [
        {"name": "n", "type": "noiseTOP", "parameters": {"period": 3}},
        {"name": "o", "type": "nullTOP", "inputs": ["n"]}]}}], new="fresh", out=str(out))
    assert res["ok"], res
    project = tr.load(str(out))
    assert project.params_of(project.node("/fresh/n")) == {"period": 3}
    assert project.inputs_of(project.node("/fresh/o")) == [{"index": 0, "from": "/fresh/n"}]
    again = tw.edit(str(out), [{"op": "set_params", "path": "/fresh/n", "params": {"period": 4}}],
                    out=str(tmp_path / "fresh2.tox"))
    assert again["ok"] and again["changed"]["changed"][0]["params"]["period"] == {"before": 3, "after": 4}
    assert out.read_bytes()  # the source is still there, unchanged by the second edit
