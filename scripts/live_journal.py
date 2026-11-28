#!/usr/bin/env python3
"""Prove the call journal against TouchDesigner: change things through the
bridge, read the journal, send its revert plan, and find the old state.

    python scripts/td_live.py launch
    python scripts/live_journal.py         # exit status 1 when a check fails

Everything lives in /project1/zz_journal; the last revert deletes it, and the
script deletes it anyway if a check failed half-way. The entries it makes stay
in the journal (they are real changes) and are marked by that path.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import td_live  # noqa: E402
from td_mcp import journal  # noqa: E402

SCRATCH = "/project1/zz_journal"
RESULTS: list[tuple[str, bool, str]] = []


def call(tool, args):
    return td_live.call(tool, args)


def check(label, got, want):
    ok = got == want
    RESULTS.append((label, ok, "" if ok else "got %r, want %r" % (got, want)))


def py(code):
    return call("execute_python", {"code": code}).get("result")


def send(plan):
    out = call("batch", {"ops": plan["ops"]})   # as they are: each keeps its `undoes`
    bad = [r for r in out.get("batch", []) if (r.get("result") or {}).get("ok") is False or r.get("ok") is False]
    return out, bad


def main() -> int:
    if not td_live.status():
        print("TouchDesigner is not answering: run `python scripts/td_live.py launch` first")
        return 2
    t0 = time.time() - 1
    try:
        call("create_node", {"path": "/project1", "type": "baseCOMP", "name": "zz_journal"})
        call("create_node", {"path": SCRATCH, "type": "noiseTOP", "name": "noise1"})
        call("create_node", {"path": SCRATCH, "type": "nullTOP", "name": "out1"})
        call("create_node", {"path": SCRATCH, "type": "textDAT", "name": "code"})
        call("create_node", {"path": SCRATCH, "type": "baseCOMP", "name": "inner"})
        call("create_node", {"path": SCRATCH + "/inner", "type": "constantCHOP", "name": "k"})
        call("set_parameters", {"path": SCRATCH + "/noise1", "params": {"period": 2, "seed": {"expr": "absTime.frame"}}})
        call("set_dat_text", {"path": SCRATCH + "/code", "text": "def f():\n\treturn 1\n"})
        baseline = py("n = op('%s/noise1')\nresult = [n.par.period.eval(), n.par.seed.expr, str(n.par.seed.mode), "
                      "n.bypass, n.name, op('%s/code').text, [i.name for i in op('%s/out1').inputs]]"
                      % (SCRATCH, SCRATCH, SCRATCH))
        # --- the changes to undo ------------------------------------------------
        call("set_parameters", {"path": SCRATCH + "/noise1", "params": {"period": 9, "seed": 4}})
        call("set_flags", {"path": SCRATCH + "/noise1", "flags": {"bypass": True}})
        call("connect_nodes", {"from_path": SCRATCH + "/noise1", "to_path": SCRATCH + "/out1"})
        call("set_dat_text", {"path": SCRATCH + "/code", "text": "def f():\n\treturn 2\n"})
        call("delete_node", {"path": SCRATCH + "/inner"})
        call("rename_node", {"path": SCRATCH + "/noise1", "new_name": "noise_renamed"})
        changes = journal.query(limit=6, path=SCRATCH, since=t0)
        check("the journal holds the six changes, newest first",
              [e["tool"] for e in changes["entries"]],
              ["rename_node", "delete_node", "set_dat_text", "connect_nodes", "set_flags", "set_parameters"])
        last = changes["entries"]
        check("old parameter state recorded",
              last[-1]["before"]["pars"].get("seed", {}).get("mode"), "EXPRESSION")
        check("deleted COMP saved as a .tox", bool(last[1]["before"].get("tox_blob")), True)
        session = last[0]["boot"]
        plan = journal.revert_plan(6, path=SCRATCH, session=session)
        check("a plan for all six, nothing it could not invert", [len(plan["undoes"]), plan.get("stopped_at")], [6, None])
        _out, bad = send(plan)
        check("the plan's calls all succeed", bad, [])
        now = py("n = op('%s/noise1')\nresult = [n.par.period.eval(), n.par.seed.expr, str(n.par.seed.mode), "
                 "n.bypass, n.name, op('%s/code').text, [i.name for i in op('%s/out1').inputs]] if n else None"
                 % (SCRATCH, SCRATCH, SCRATCH))
        check("parameters, expression, flag, name, text and wiring are back", now, baseline)
        inner = py("i = op('%s/inner')\nresult = [i.OPType, [c.name for c in i.children]] if i else None" % SCRATCH)
        check("the deleted COMP is back with its contents", inner, ["baseCOMP", ["k"]])
        # --- undo the setup too: the scratch COMP goes away ---------------------
        again = journal.revert_plan(6, path=SCRATCH, session=session)
        check("asking again walks further back, not into the revert",
              [o["tool"] for o in again["ops"]][:2], ["set_dat_text", "set_parameters"])
        rest = journal.revert_plan(50, path=SCRATCH, session=session)
        _out, bad = send(rest)
        check("reverting the setup deletes what it created",
              [bad, py("result = op('%s') is None" % SCRATCH)], [[], True])
    finally:
        try:
            py("o = op('%s')\nif o: o.destroy()\nresult = True" % SCRATCH)
        except Exception:  # noqa: BLE001
            pass
    failed = [r for r in RESULTS if not r[1]]
    for label, ok, why in RESULTS:
        print("%s  %s%s" % ("ok  " if ok else "FAIL", label, ("  -- " + why) if why else ""))
    print("%d/%d checks passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
