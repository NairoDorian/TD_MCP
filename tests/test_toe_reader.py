"""The offline .toe/.tox reader (`td_mcp.toe_reader`).

Every fixture below is the on-disk form TouchDesigner 2025.33230 wrote for a
network built for the purpose and expanded with its own `toeexpand` (see
UPSTREAM_SYNC_2026-11-26.md); the bytes are reproduced here so the suite needs
neither TouchDesigner nor toeexpand.
"""

import json
import struct
import subprocess

import pytest

from td_mcp import server_offline as so
from td_mcp import toe_reader as tr
from td_mcp.kb import param_catalog

needs_catalog = pytest.mark.skipif(not param_catalog.available(),
                                   reason="parameter catalog not built")


# --------------------------------------------------------------------------
# Fixture: an expanded component, as toeexpand writes it
# --------------------------------------------------------------------------
def text_payload(text):
    body = text.encode("utf-8")
    return b"2\n*" + struct.pack(">6I", 1, 1, 1, 1, 2, len(body)) + body


def table_payload(rows):
    cols = len(rows[0]) if rows else 0
    out = b"1\n*" + struct.pack(">4I", 1, len(rows), cols, 0)
    for row in rows:
        for cell in row:
            data = cell.encode("utf-8")
            out += struct.pack(">2I", 2, len(data)) + data
    return out


def node_file(kind, x=0, y=0, flags="parlanguage 0", inputs=(), extra=()):
    lines = [kind]
    lines += list(extra)
    lines.append("tile %d %d 130 90" % (x, y))
    lines.append("flags =  %s" % flags)
    if inputs:
        lines += ["inputs", "{"] + ["%d \t%s" % (i, s) for i, s in inputs] + ["}"]
    lines += ["color 0.67 0.67 0.67 ", "end"]
    return "\n".join(lines) + "\n"


def parm_file(*lines):
    return "?\n" + "".join(line + "\n" for line in lines) + "?\n"


FILES = {
    ".build": "version 099\nbuild 2025.33230\ntime Thu Nov 26 23:40:00 2026\nosname Windows\nosversion 10\n",
    "demo.n": node_file("COMP:base", 800, -600),
    "demo/noise1.n": node_file("TOP:noise", 0, 0, "viewer 1 parlanguage 0",
                               extra=['comment "a comment"', "tags 0 2 alpha beta",
                                      "dict 8004950A000000000000007D948C016B944B05732E"]),
    "demo/noise1.parm": parm_file("period 0 2.5", "mono 0 off", "resolutionw 0 640",
                                  "tz 17 0 \"op('null_ctrl')['chan1'] * 2\""),
    "demo/xf.n": node_file("TOP:transform", 200, 0, "bypass on parlanguage 0", [(0, "noise1")]),
    "demo/xf.parm": parm_file("tx 0 0.25", 'ty 17 0 "absTime.seconds * 0.1"',
                              "rotate 515 0 op('holder').par.Amount", "sx 0 2",
                              "extend 0 hold"),
    "demo/comp1.n": node_file("TOP:comp", 400, 0, inputs=[(0, "noise1"), (1, "xf")]),
    "demo/out1.n": node_file("TOP:out", 600, 0, inputs=[(0, "comp1")]),
    "demo/out1.parm": parm_file("label 16 trails me.name"),
    "demo/geo1.n": node_file("COMP:geo", 0, -200, "render on display on pickable on parlanguage 0"),
    "demo/geo1/torus1.n": node_file("SOP:torus"),
    "demo/inner.n": node_file("COMP:base", 400, -200),
    "demo/inner.network": "1\ncompinputs\n{\n0 \txf\n\tin1\n\tTOP\n}\nend\n",
    "demo/inner/in1.n": node_file("TOP:in"),
    "demo/inner/out1.n": node_file("TOP:out", 200, 0, inputs=[(0, "in1")]),
    "demo/after.n": node_file("TOP:null", 600, -200, inputs=[(0, "inner/out1")]),
    "demo/holder.n": node_file("COMP:base", 0, -400),
    "demo/holder.cparm": (
        "?\npages 1 Controls\n"
        '772804865 Amount Amount 1 1 0 0 1 1 1 2 0 "" "" Controls 0\n'
        '772804879 Kind Kind 1 1 0 0 1 1 1 2 0 "" "" Controls 4097 2 one "Label 1" two "Label 2" 1\n'
        '772804868 Label Label 1 1 0 0 1 1 1 2 0 "" "" Controls 2\n'
        '772804867 Enable Enable 1 1 0 0 1 1 1 2 0 "" "" Controls 3\n'
        '772813569 Pos Pos 1 1 0 0 1 1 1 1 0 0 1 1 1 1 0 0 1 1 1 2 0 "" "" 2 0 "" "" 2 0 "" "" Controls 4\n'
        '772804869 Go Go 1 1 0 0 1 1 1 2 0 "" "" Controls 5\n?\n'),
    "demo/holder.parm": parm_file("Amount 67108928 0.75", 'Label 67108928 "two words"'),
    "demo/txt.n": node_file("DAT:text", 0, -600),
    "demo/const1.n": node_file("CHOP:constant", 200, -600),
    "demo/const1.parm": parm_file("const0name 0 a", "const0value 0 1", "const1name 0 b",
                                  "const1value 2 0.5"),
}
BINARY = {
    "demo/txt.text": text_payload("line one\nline two\n# end"),
    "demo/t32.table": table_payload([["a", "bb"], ["ccc", "dddd"], ["eeeee", "ffffff"]]),
}
FILES["demo/t32.n"] = node_file("DAT:table", 400, -600)


def write_tree(root, files=FILES, binary=BINARY):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content.encode("utf-8"))
    for rel, content in binary.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    return root


@pytest.fixture
def project(tmp_path):
    return tr.load(str(write_tree(tmp_path / "demo.tox.dir")))


# --------------------------------------------------------------------------
# Grammars
# --------------------------------------------------------------------------
def test_mode_is_the_low_two_bits_not_the_expression_bit():
    # 16 = "an expression is stored", mode constant: the Out TOP label that was
    # overwritten with a value. Reading 0x10 as the mode reports an expression.
    rec = tr.parse_parm_line("label 16 trails me.name")
    assert rec["mode"] == "constant" and rec["value"] == "trails" and rec["expr"] == "me.name"
    rec = tr.parse_parm_line('ty 17 0 "absTime.seconds * 0.1"')
    assert rec["mode"] == "expression" and rec["expr"] == "absTime.seconds * 0.1"
    rec = tr.parse_parm_line("rotate 515 0 op('holder').par.Amount")
    assert rec["mode"] == "bind" and rec["bind"] == "op('holder').par.Amount"
    assert tr.parse_parm_line("fromrange1 2 0")["mode"] == "export"


def test_value_count_comes_from_the_flags_word():
    # Bind mode, no expression: the tail is constant + bind, never one constant.
    rec = tr.parse_parm_line("colorr 515 1 parent.Checker.par.Color1r")
    assert rec["value"] == "1" and rec["bind"] == "parent.Checker.par.Color1r"
    # Constant mode: the whole remainder, spaces and all.
    assert tr.parse_parm_line("label 0 some words with spaces")["value"] == "some words with spaces"
    assert tr.parse_parm_line('Label 67108928 "two words"')["value"] == "two words"
    # Single quotes are content; a shell-style splitter would eat them.
    rec = tr.parse_parm_line("fillcolorr 17 1 op('circle_red').par.value0")
    assert rec["expr"] == "op('circle_red').par.value0"
    # Expression + bind + the repeated extra value.
    rec = tr.parse_parm_line("Pivotdistance 471859779 0 \"(me, 'PivotDistance')\" \"(me, 'PivotDistance')\"")
    assert rec["mode"] == "bind" and rec["bind"] == "(me, 'PivotDistance')"
    # An expression stored as the empty string is present, not absent.
    rec = tr.parse_parm_line('clone 17 "" "op.TDAnnotate.op(\'annotate\')"')
    assert rec["value"] == "" and rec["expr"].startswith("op.TDAnnotate")
    assert tr.parse_parm_line("?") is None and tr.parse_parm_line("name notanumber x") is None


def test_quoted_value_spans_lines():
    text = parm_file("pageindex 0 4", 'text 0 "first', "second", '"', "type 0 multiline")
    recs = {r["name"]: r for r in tr.parse_parms(text)}
    assert recs["text"]["value"] == "first\nsecond\n"
    assert recs["type"]["value"] == "multiline"
    text = parm_file('Bodytext 67108928 "changed:', 'radius: 0.5"', 'Titletext 67108928 "t"')
    recs = {r["name"]: r for r in tr.parse_parms(text)}
    assert recs["Bodytext"]["value"] == "changed:\nradius: 0.5" and recs["Titletext"]["value"] == "t"


def test_legacy_parm_grammar():
    text = '{\nversion 0.8\nstdswitcher\t(\t1\t1\t)\npanelfill\t(\t"off"\t)\npaneltop\t(\t./icon\t)\n}\n'
    recs = {r["name"]: r for r in tr.parse_parms(text)}
    assert recs["panelfill"]["value"] == "off" and recs["paneltop"]["value"] == "./icon"
    assert recs["stdswitcher"]["value"] == "1 1" and recs["panelfill"]["legacy"]
    assert tr.parse_parms("{\nversion 0.8\n}\n") == []


def test_node_file():
    n = tr.parse_node(FILES["demo/noise1.n"])
    assert (n["family"], n["type"]) == ("TOP", "noise")
    assert (n["x"], n["y"]) == (0, 0)
    assert n["flags"] == {"viewer": 1, "parlanguage": 0}
    assert n["comment"] == "a comment" and n["tags"] == ["alpha", "beta"]
    assert n["has_storage"] is True  # noted, never unpickled
    n = tr.parse_node(FILES["demo/comp1.n"])
    assert n["inputs"] == [(0, "noise1"), (1, "xf")]
    n = tr.parse_node("DAT:table\ntile 1 2 3 4\nflags =  viewer 1 showDocked off\ndock trigger1\nend\n")
    assert n["dock"] == "trigger1" and n["flags"]["showDocked"] is False
    n = tr.parse_node('CHOP:math\ntile 0 0 1 1\nflags =  parlanguage 0\nexports\n{\n"lag2\nlag1"\n}\n\nend\n')
    assert n["exports"] == ["lag2", "lag1"]
    n = tr.parse_node('COMP:base\ncomment "two\nlines"\ntile 5 6 7 8\nflags =  parlanguage 0\nend\n')
    assert n["comment"] == "two\nlines" and n["x"] == 5


def test_text_payload_uses_the_length_field():
    assert tr.read_text_payload(text_payload("line one\nline two\n# end")) == "line one\nline two\n# end"
    # The first payload byte is a newline; a reader that scans for one is off.
    assert tr.read_text_payload(text_payload("\nx = 1\n")) == "\nx = 1\n"
    # An empty DAT is a 19-byte header with no length word at all.
    assert tr.read_text_payload(b"2\n*" + struct.pack(">4I", 1, 0, 0, 1)) == ""


def test_table_payload_is_rows_then_columns():
    rows = [["a", "bb"], ["ccc", "dddd"], ["eeeee", "ffffff"]]
    data = table_payload(rows)
    assert data[3:19] == struct.pack(">4I", 1, 3, 2, 0)  # as saved: 3 rows, 2 columns
    assert tr.read_table_payload(data) == rows
    assert tr.read_table_payload(b"") == [] and tr.read_table_payload(b"2\n*junk") == []
    assert tr.read_table_payload(data[:-3])[2] == ["eeeee", "fff"]  # truncated file: no raise


def test_custom_parameter_definitions():
    c = tr.parse_cparms(FILES["demo/holder.cparm"])
    assert c["pages"] == ["Controls"]
    by = {p["name"]: p for p in c["pars"]}
    assert [by[n]["style"] for n in ("Amount", "Kind", "Label", "Enable", "Go")] == \
        ["Float", "Menu", "Str", "Toggle", "Pulse"]
    assert by["Kind"]["menu"] == ["one", "two"] and by["Kind"]["menu_labels"] == ["Label 1", "Label 2"]
    # appendXYZ: type code 0x2E103301 = Float, three components, named x y z.
    assert by["Pos"]["style"] == "XYZ" and by["Pos"]["size"] == 3
    assert by["Pos"]["names"] == ["Posx", "Posy", "Posz"]
    assert all(p["page"] == "Controls" for p in c["pars"])
    c = tr.parse_cparms('?\npages 2 "TD MCP" About\n772804868 Token "Auth Token" 1 1 0 0 1 1 1 2 0 "" "" "TD MCP" 2\n?\n')
    assert c["pages"] == ["TD MCP", "About"]
    assert c["pars"][0] == {"name": "Token", "label": "Auth Token", "style": "Str", "page": "TD MCP"}


# --------------------------------------------------------------------------
# Project model
# --------------------------------------------------------------------------
@needs_catalog
def test_saved_type_names_resolve_through_the_catalog(project):
    # The file stores OP.type; create() wants OP.OPType, and the short form is
    # not a prefix of it for about one operator in six.
    assert project.node("/demo/geo1").op_type == "geometryCOMP"
    assert project.node("/demo/comp1").op_type == "compositeTOP"
    assert project.node("/demo/noise1").op_type == "noiseTOP"
    assert tr.op_type_for("DAT", "eval") == "evaluateDAT"
    assert tr.op_type_for("DAT", "parexec") == "parameterexecuteDAT"
    assert tr.op_type_for("TOP", "nosuchthing") == "nosuchthingTOP"


def test_tree_and_wiring(project):
    assert project.roots == ["/demo"] and project.kind == "tox"
    assert project.build["build"] == "2025.33230"
    assert {n.name for n in project.children("/demo")} >= {"noise1", "xf", "geo1", "inner", "holder"}
    assert [n.path for n in project.children("/demo/geo1")] == ["/demo/geo1/torus1"]
    wires = project.inputs_of(project.node("/demo/comp1"))
    assert wires == [{"index": 0, "from": "/demo/noise1"}, {"index": 1, "from": "/demo/xf"}]
    # A COMP feeding a TOP is written "inner/out1": the wire belongs to the COMP.
    assert project.inputs_of(project.node("/demo/after")) == \
        [{"index": 0, "from": "/demo/inner", "via_out": "out1"}]
    # A COMP's own inputs live in its .network file.
    assert project.inputs_of(project.node("/demo/inner")) == \
        [{"index": 0, "from": "/demo/xf", "to_in": "in1"}]
    assert len(list(project.walk("/demo", depth=1))) == len(project.children("/demo"))
    assert len(list(project.walk(None))) == len(project.nodes)


@needs_catalog
def test_parameters_as_a_tool_call_would_send_them(project):
    p = project.params_of(project.node("/demo/noise1"))
    assert p == {"period": 2.5, "mono": False, "resolutionw": 640,
                 "tz": {"expr": "op('null_ctrl')['chan1'] * 2"}}
    p = project.params_of(project.node("/demo/xf"))
    assert p["rotate"] == {"bind": "op('holder').par.Amount"} and p["extend"] == "hold"
    assert project.params_of(project.node("/demo/out1")) == {"label": "trails"}
    p = project.params_of(project.node("/demo/holder"))
    assert p == {"Amount": 0.75, "Label": "two words"}
    p = project.params_of(project.node("/demo/const1"))
    assert p["const1name"] == "b" and p["const1value"] == {"export": True, "value": 0.5}


def test_dat_contents(project):
    assert project.node("/demo/txt").text == "line one\nline two\n# end"
    assert project.node("/demo/t32").table[1] == ["ccc", "dddd"]
    assert project.node("/demo/noise1").text is None and project.node("/demo/noise1").table is None


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------
def test_summary(project):
    s = tr.summarize(project)
    assert s["operators"] == len(project.nodes) and s["kind"] == "tox"
    assert s["roots"][0]["path"] == "/demo" and s["roots"][0]["operators"] == len(project.nodes) - 1
    assert s["expressions"] == 2 and s["binds"] == 1  # the inactive `me.name` is not counted
    assert s["dats_with_text"] == 1 and s["comps_with_custom_parameters"] == 1
    assert s["by_family"]["TOP"] == 7


def test_read_network(project):
    out = tr.read_network(project, "/demo", depth=1)
    assert out["ok"] and out["count"] == len(project.children("/demo"))
    by = {o["path"]: o for o in out["operators"]}
    assert by["/demo/xf"]["flags"] == ["bypass"]
    assert by["/demo/geo1"]["flags"] == ["render", "display", "pickable"] and by["/demo/geo1"]["children"] == 1
    assert by["/demo/noise1"]["comment"] == "a comment" and by["/demo/noise1"]["has_storage"]
    assert by["/demo/txt"]["text_lines"] == 3 and "text" not in by["/demo/txt"]
    assert by["/demo/t32"]["table_shape"] == [3, 2]
    assert by["/demo/holder"]["custom_pages"] == ["Controls"]
    full = tr.read_network(project, "/demo", depth=2, text="full")
    assert full["count"] == len(project.nodes) - 1
    assert {o["path"]: o for o in full["operators"]}["/demo/txt"]["text"].startswith("line one")
    capped = tr.read_network(project, "/demo", depth=3, max_ops=3)
    assert len(capped["operators"]) == 3 and "truncated" in capped
    missing = tr.read_network(project, "/demo/noize1")
    assert missing["ok"] is False and "/demo/noise1" in missing["did_you_mean"]


def test_grep(project):
    hit = tr.grep(project, r"line t\w+")
    assert hit["matches"] == [{"path": "/demo/txt", "where": "text", "line": 2, "match": "line two"}]
    hit = tr.grep(project, "holder", scope="params")
    assert hit["matches"][0]["par"] == "rotate" and hit["matches"][0]["mode"] == "bind"
    assert {m["where"] for m in tr.grep(project, "dddd")["matches"]} == {"table"}
    assert tr.grep(project, "^geo", scope="names")["matches"][0]["path"] == "/demo/geo1"
    assert tr.grep(project, "comment", scope="names")["matches"][0]["where"] == "comment"
    assert tr.grep(project, "noise1", scope="names", path="/demo/inner")["count"] == 0
    assert tr.grep(project, "(")["ok"] is False


def test_diff_reports_meaning_and_sets_moves_apart(tmp_path, project):
    files = dict(FILES)
    files["demo/xf.parm"] = parm_file("tx 0 0.5", 'ty 17 0 "absTime.seconds * 0.1"', "sx 0 2", "extend 0 hold")
    files["demo/comp1.n"] = node_file("TOP:comp", 400, 0, inputs=[(0, "xf")])
    files["demo/noise1.n"] = files["demo/noise1.n"].replace("tile 0 0", "tile 55 66")
    files["demo/extra.n"] = node_file("TOP:null", 900, 0)
    del files["demo/after.n"]
    binary = dict(BINARY)
    binary["demo/txt.text"] = text_payload("line one\nline 2\n# end")
    other = tr.load(str(write_tree(tmp_path / "after" / "demo.tox.dir", files, binary)))
    d = tr.diff(project, other)
    assert d["summary"] == {"added": 1, "removed": 1, "changed": 3, "moved_only": 1}
    assert d["added"][0]["path"] == "/demo/extra" and d["removed"][0]["path"] == "/demo/after"
    assert d["moved_only"] == ["/demo/noise1"]
    by = {c["path"]: c for c in d["changed"]}
    assert by["/demo/xf"]["params"]["tx"] == {"before": 0.25, "after": 0.5}
    assert by["/demo/xf"]["params"]["rotate"]["after"] == "<default>"
    assert by["/demo/comp1"]["rewired"]["after"] == ["0<-/demo/xf"]
    assert any(line == "-line two" for line in by["/demo/txt"]["text"])
    assert tr.diff(project, project)["summary"] == {"added": 0, "removed": 0, "changed": 0, "moved_only": 0}


@needs_catalog
def test_check_against_the_catalog(tmp_path, project):
    assert tr.check(project)["clean"], tr.check(project)
    files = dict(FILES)
    files["demo/noise1.parm"] = parm_file("period 0 2.5", "frequency 0 3", "type 0 simplex5d",
                                          "pageindex1 0 2")
    files["demo/old.n"] = node_file("SOP:font")
    bad = tr.check(tr.load(str(write_tree(tmp_path / "bad" / "demo.tox.dir", files))))
    assert not bad["clean"]
    assert bad["unknown_types"] == {"SOP:font": 1}
    assert [(u["par"], u["type"]) for u in bad["unknown_parameters"]] == [("frequency", "noiseTOP")]
    assert bad["invalid_menu_values"][0]["value"] == "simplex5d"
    assert "simplex3d" in param_catalog.par("noiseTOP", "type")["menu"]


@needs_catalog
def test_spec_round_trips_into_the_validator(project):
    spec = tr.to_spec(project, "/demo")
    by = {o["name"]: o for o in spec["operators"]}
    assert by["comp1"]["inputs"] == ["noise1", "xf"] and by["comp1"]["type"] == "compositeTOP"
    assert by["xf"]["flags"] == {"bypass": True}
    assert by["noise1"]["parameters"]["tz"] == {"expr": "op('null_ctrl')['chan1'] * 2"}
    assert by["txt"]["text"].startswith("line one")
    assert spec["nested_comps"] == ["geo1", "inner"]
    report = json.loads(so.td_validate_build(json.dumps(spec["operators"])))
    codes = {i.get("code") for i in report.get("issues", [])}
    assert "UNKNOWN_TYPE" not in codes and "PARAM_UNKNOWN" not in codes, report
    assert tr.to_spec(project, "/nope")["ok"] is False


# --------------------------------------------------------------------------
# Expansion (toeexpand faked: it is TouchDesigner's, not ours)
# --------------------------------------------------------------------------
def test_expand_works_on_a_copy_and_caches(tmp_path, monkeypatch):
    src = tmp_path / "work" / "show.toe"
    src.parent.mkdir()
    src.write_bytes(b"not really a toe")
    calls = []

    def fake_run(cmd, cwd=None, **_kw):
        calls.append(cwd)
        write_tree(tmp_path.__class__(cwd) / (cmd[1] + ".dir"))
        return subprocess.CompletedProcess(cmd, 1, b"", b"")  # exit 1 IS success

    monkeypatch.setattr(tr, "find_toeexpand", lambda: "toeexpand")
    monkeypatch.setattr(tr.subprocess, "run", fake_run)
    cache = tmp_path / "cache"
    out = tr.expand(str(src), cache_dir=str(cache))
    assert out.endswith("show.toe.dir") and str(cache) in out
    assert sorted(p.name for p in src.parent.iterdir()) == ["show.toe"]  # original untouched
    assert tr.expand(str(src), cache_dir=str(cache)) == out and len(calls) == 1  # cached
    tr.expand(str(src), cache_dir=str(cache), refresh=True)
    assert len(calls) == 2
    src.write_bytes(b"changed")
    assert tr.expand(str(src), cache_dir=str(cache)) != out  # keyed by content
    assert tr.load(str(src), cache_dir=str(cache)).source == "show.toe"


def test_expand_failures_are_explained(tmp_path, monkeypatch):
    with pytest.raises(tr.ToeReaderError, match="no such file"):
        tr.expand(str(tmp_path / "missing.toe"))
    other = tmp_path / "notes.txt"
    other.write_text("x")
    with pytest.raises(tr.ToeReaderError, match="not a .toe or .tox"):
        tr.expand(str(other))
    src = tmp_path / "a.tox"
    src.write_bytes(b"x")
    monkeypatch.setattr(tr, "find_toeexpand", lambda: None)
    with pytest.raises(tr.ToeReaderError, match="TD_MCP_TOEEXPAND"):
        tr.expand(str(src), cache_dir=str(tmp_path / "c"))
    monkeypatch.setattr(tr, "find_toeexpand", lambda: "toeexpand")
    monkeypatch.setattr(tr.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, b"", b"Error opening file"))
    with pytest.raises(tr.ToeReaderError, match="Error opening file"):
        tr.expand(str(src), cache_dir=str(tmp_path / "c"))
    assert not any((tmp_path / "c").iterdir())  # a failed slot is not left behind


def test_cache_is_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "find_toeexpand", lambda: "toeexpand")
    monkeypatch.setattr(tr.subprocess, "run", lambda cmd, cwd=None, **kw: (
        write_tree(tmp_path.__class__(cwd) / (cmd[1] + ".dir"), {".build": "build 1\n"}, {}),
        subprocess.CompletedProcess(cmd, 1, b"", b""))[1])
    for i in range(5):
        f = tmp_path / ("p%d.tox" % i)
        f.write_bytes(b"content %d" % i)
        tr.expand(str(f), cache_dir=str(tmp_path / "cache"), keep=3)
    assert len(list((tmp_path / "cache").iterdir())) == 3


# --------------------------------------------------------------------------
# The offline tools
# --------------------------------------------------------------------------
def test_tools_answer_for_an_expanded_tree(tmp_path):
    root = str(write_tree(tmp_path / "demo.tox.dir"))
    info = json.loads(so.td_project_read(root))
    assert info["ok"] and info["operators"] == 15 and info["roots"][0]["path"] == "/demo"
    net = json.loads(so.td_project_read(root, path="/demo", depth=1))
    assert net["count"] == 11
    spec = json.loads(so.td_project_read(root, path="/demo", format="spec"))
    assert spec["ok"] and len(spec["operators"]) == 11
    assert json.loads(so.td_project_read(root, format="spec"))["ok"] is False
    assert json.loads(so.td_project_grep(root, "line two"))["count"] == 1
    assert json.loads(so.td_project_diff(root, root))["summary"]["changed"] == 0
    assert json.loads(so.td_project_read(str(tmp_path / "nope.toe")))["ok"] is False
    assert json.loads(so.td_project_read(""))["ok"] is False
    for name in ("td_project_read", "td_project_grep", "td_project_diff", "td_project_check"):
        assert name in so._OFFLINE_DISPATCH


def test_large_listing_is_cut_with_advice(tmp_path):
    files = dict(FILES)
    for i in range(400):
        files["demo/n%03d.n" % i] = node_file("TOP:null", i * 10, 0)
        files["demo/n%03d.parm" % i] = parm_file("label 0 " + "x" * 300)
    root = str(write_tree(tmp_path / "big.tox.dir", files))
    # 411 operators: the reader shows 150 and says so ...
    listing = json.loads(so.td_project_read(root, path="/demo", depth=1, params=False))
    assert len(listing["operators"]) == 150 and listing["count"] == 411
    assert "150 of 411" in listing["truncated"]
    # ... and a reply that is still too long is cut with advice, not dropped.
    out = so._compact({"operators": ["x" * 100] * 1000}, max_chars=5000)
    assert len(out) < 5200 and "narrow `path`" in out
