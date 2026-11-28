#!/usr/bin/env python3
"""Prove the offline .toe/.tox editor against TouchDesigner itself.

The unit tests check that ``td_mcp.toe_writer`` writes the bytes
TouchDesigner writes. This script checks that TouchDesigner *reads* them the
way the edit meant:

  1. TouchDesigner builds a small component in /project1/zz_toe_edit (constant,
     expression, bind and custom parameters, a text DAT, a table, flags,
     wires), saves it as a .tox and deletes it;
  2. ``toe_writer.edit`` changes that file with TouchDesigner's hands off it;
  3. TouchDesigner loads the result and every change is read back.

    python scripts/td_live.py launch
    python scripts/live_toe_edit.py          # exit status 1 when a check fails

Everything lives in /project1/zz_toe_edit and a temporary folder; both are
removed at the end.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import td_live  # noqa: E402
from td_mcp import toe_writer as tw  # noqa: E402

SCRATCH = "/project1/zz_toe_edit"
RESULTS: list[tuple[str, bool, str]] = []


def py(code: str):
    out = td_live.call("execute_python", {"code": code})
    if not out.get("ok"):
        raise SystemExit("execute_python failed: %s" % json.dumps(out)[:600])
    return out.get("result")


def check(label: str, got, want) -> None:
    ok = got == want
    RESULTS.append((label, ok, "" if ok else "got %r, want %r" % (got, want)))


BUILD = r'''
root = op('/project1')
old = root.op('zz_toe_edit')
if old:
    old.destroy()
c = root.create(baseCOMP, 'zz_toe_edit')
n = c.create(noiseTOP, 'noise1')
n.par.period = 2
n.par.seed.expr = 'absTime.frame'
lv = c.create(levelTOP, 'level1')
lv.inputConnectors[0].connect(n)
out = c.create(nullTOP, 'out1')
out.inputConnectors[0].connect(lv)
d = c.create(textDAT, 'code')
d.text = "def f():\n\treturn 1\n"
t = c.create(tableDAT, 'table1')
t.clear()
t.appendRow(['key', 'value'])
t.appendRow(['speed', '1'])
k = c.create(constantCHOP, 'const1')
k.par.const0name = 'a'
h = c.create(baseCOMP, 'holder')
pg = h.appendCustomPage('Controls')
pg.appendFloat('Amount')[0].val = 0.25
pg.appendStr('Label')[0].val = 'two words'
m = pg.appendMenu('Kind')[0]
m.menuNames = ['one', 'two']
m.menuLabels = ['One', 'Two']
pg.appendXYZ('Pos')
lv.par.opacity.bindExpr = "op('holder').par.Amount"
c.save(PATH)
c.destroy()
result = True
'''

EDITS = [
    {"op": "set_params", "path": "/zz_toe_edit/noise1",
     "params": {"period": 7.5, "seed": 3, "type": "random", "t": [0.1, 0.2, 0.3]}},
    {"op": "set_params", "path": "/zz_toe_edit/level1", "params": {"brightness1": {"expr": "1 + 0.5"}}},
    {"op": "set_params", "path": "/zz_toe_edit/holder",
     "params": {"Amount": 0.75, "Label": 'say "hi" é', "Kind": "two", "Pos": [1, 2, 3]}},
    {"op": "set_params", "path": "/zz_toe_edit/const1", "params": {"const1name": "b", "const1value": 0.5}},
    {"op": "set_text", "path": "/zz_toe_edit/code", "text": "def f():\n\treturn 'café'\n"},
    {"op": "set_cells", "path": "/zz_toe_edit/table1", "cells": [{"row": "speed", "col": "value", "value": 4}]},
    {"op": "set_flags", "path": "/zz_toe_edit/noise1", "flags": {"bypass": True}},
    {"op": "set_node", "path": "/zz_toe_edit/out1", "comment": "edited offline", "position": [800, -200]},
    {"op": "add", "parent": "/zz_toe_edit", "name": "blur1", "type": "blurTOP",
     "params": {"size": 9}, "inputs": ["level1"]},
    {"op": "connect", "from": "/zz_toe_edit/blur1", "to": "/zz_toe_edit/out1", "input": 0},
    {"op": "add", "parent": "/zz_toe_edit", "name": "geo1", "type": "geometryCOMP"},
    {"op": "build", "parent": "/zz_toe_edit/geo1", "spec": {"operators": [
        {"name": "box1", "type": "boxSOP", "flags": {"display": True, "render": True}}]}},
    {"op": "rename", "path": "/zz_toe_edit/table1", "name": "settings"},
]

READ = r'''
c = op('/project1').loadTox(PATH)
def pv(o, n):
    p = o.par[n]
    return [str(p.mode).split('.')[-1], p.eval()] if p is not None else None
n = c.op('noise1'); lv = c.op('level1'); h = c.op('holder'); k = c.op('const1')
s = c.op('settings'); g = c.op('geo1'); out = c.op('out1')
for x in (out, c.op('blur1')):
    x.cook(force=True)
result = {
    'path': c.path,
    'period': pv(n, 'period'), 'seed': pv(n, 'seed'), 'seed_expr': n.par.seed.expr, 'type': pv(n, 'type'),
    't': [n.par.tx.eval(), n.par.ty.eval(), n.par.tz.eval()], 'bypass': n.bypass,
    'brightness1': [pv(lv, 'brightness1'), lv.par.brightness1.expr],
    'opacity_bound': [str(lv.par.opacity.mode).split('.')[-1], lv.par.opacity.eval()],
    'holder': [h.par.Amount.eval(), h.par.Label.eval(), h.par.Kind.eval(),
               [h.par.Posx.eval(), h.par.Posy.eval(), h.par.Posz.eval()]],
    'const': [k.par.const.sequence.numBlocks, k.par.const1name.eval(), k.par.const1value.eval()],
    'code': c.op('code').text,
    'settings': [[s[r, q].val for q in range(s.numCols)] for r in range(s.numRows)] if s else None,
    'table1_gone': c.op('table1') is None,
    'out': [[i.name for i in out.inputs], out.comment, out.nodeX, out.nodeY],
    'blur': [c.op('blur1').OPType, pv(c.op('blur1'), 'size'), [i.name for i in c.op('blur1').inputs]],
    'geo': [g.render, g.display, [(x.name, x.OPType, x.display, x.render) for x in g.children]],
    'errors': c.errors(recurse=True),
}
c.destroy()
'''


def main() -> int:
    if not td_live.status():
        print("TouchDesigner is not answering: run `python scripts/td_live.py launch` first")
        return 2
    work = Path(tempfile.mkdtemp(prefix="td_mcp_toe_edit_"))
    try:
        src, dst = work / "zz_toe_edit.tox", work / "zz_toe_edit.edited.tox"
        py("PATH = %r\n" % src.as_posix() + BUILD)
        check("TouchDesigner saved the source component", src.is_file(), True)
        res = tw.edit(str(src), EDITS, out=str(dst))
        check("offline edit succeeded", res.get("ok"), True)
        if not res.get("ok"):
            print(json.dumps(res, indent=1)[:3000])
            return 1
        r = py("PATH = %r\n" % dst.as_posix() + READ)
        check("constant", r["period"], ["CONSTANT", 7.5])
        check("constant over an expression keeps the text", [r["seed"], r["seed_expr"]],
              [["CONSTANT", 3.0], "absTime.frame"])
        check("menu token", r["type"], ["CONSTANT", "random"])
        check("tuplet", r["t"], [0.1, 0.2, 0.3])
        check("bypass flag", r["bypass"], True)
        check("new expression", r["brightness1"], [["EXPRESSION", 1.5], "1 + 0.5"])
        check("untouched bind still bound, now to the edited master", r["opacity_bound"], ["BIND", 0.75])
        check("custom parameters (escaped, non-ASCII, menu, XYZ)", r["holder"],
              [0.75, 'say "hi" é', "two", [1.0, 2.0, 3.0]])
        check("sequence grown by one block", r["const"], [2, "b", 0.5])
        check("DAT text", r["code"], "def f():\n\treturn 'café'\n")
        check("table cell", r["settings"], [["key", "value"], ["speed", "4"]])
        check("renamed operator", r["table1_gone"], True)
        check("rewired + comment + position", r["out"], [["blur1"], "edited offline", 800, -200])
        check("added operator", r["blur"], ["blurTOP", ["CONSTANT", 9.0], ["level1"]])
        check("added Geometry COMP renders a built box", r["geo"], [True, True, [["box1", "boxSOP", True, True]]])
        check("no errors after loading", r["errors"], "")
    finally:
        try:
            py("o = op(%r)\nif o: o.destroy()\nresult = True" % SCRATCH)
        except SystemExit:
            pass
        shutil.rmtree(work, ignore_errors=True)
    failed = [r for r in RESULTS if not r[1]]
    for label, ok, why in RESULTS:
        print("%s  %s%s" % ("ok  " if ok else "FAIL", label, ("  -- " + why) if why else ""))
    print("%d/%d checks passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
