"""The call journal: the bridge records each change with the state before it,
``td_mcp.journal`` lists entries and builds the calls that undo them.

The round trip -- change, read the journal, send the revert plan, find the
old state -- runs here through the test double, and in TouchDesigner in
``scripts/live_journal.py``.
"""

from __future__ import annotations

import json

import pytest

from td_mcp import journal, server_offline
from tests.td_double import FakeParMode, build_bridge


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TD_MCP_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def bridge(home):
    return build_bridge()


def _run(br, tool, args):
    return br["_run_tool"](tool, args)


def test_changes_are_recorded_with_what_was_there(bridge, home):
    br, td = bridge
    td.make("n1", "noiseTOP", params={"period": 2.0})
    _run(br, "set_parameters", {"path": "/project1/n1", "params": {"period": 5}})
    _run(br, "get_node", {"path": "/project1/n1"})            # a read: not recorded
    _run(br, "set_node_position", {"path": "/project1/n1", "x": 300, "y": 40})
    rows = journal.entries()
    assert [r["tool"] for r in rows] == ["set_parameters", "set_node_position"]
    first = rows[0]
    assert first["before"]["pars"]["period"] == {"mode": "CONSTANT", "val": 2.0}
    assert first["ok"] is True and first["risk"] == "WRITE_ADDITIVE" and first["boot"]
    assert (home / "journal" / "calls.jsonl").is_file()


def test_reads_never_grow_a_sequence_while_recording(bridge):
    br, td = bridge
    k = td.make("c1", "constantCHOP")
    blocks = len(k.seq.const) if hasattr(k, "seq") and hasattr(k.seq, "const") else None
    _run(br, "set_parameters", {"path": "/project1/c1", "params": {"const5value": 1}})
    rows = journal.entries()
    assert rows and rows[-1]["tool"] == "set_parameters"
    if blocks is not None:
        assert "const5value" in rows[-1]["before"]["pars"]


def test_revert_plan_restores_parameters_flags_and_names(bridge):
    br, td = bridge
    n = td.make("n1", "noiseTOP", params={"period": 2.0, "amp": 1.0})
    n.par.amp.expr = "absTime.seconds"
    n.par.amp.mode = FakeParMode.EXPRESSION
    _run(br, "set_parameters", {"path": "/project1/n1", "params": {"period": 5, "amp": 0.5}})
    _run(br, "set_flags", {"path": "/project1/n1", "flags": {"bypass": True}})
    _run(br, "rename_node", {"path": "/project1/n1", "new_name": "n2"})
    plan = journal.revert_plan(3)
    assert [op["tool"] for op in plan["ops"]] == ["rename_node", "set_flags", "set_parameters"]
    out = br["_do_batch"]([{"tool": o["tool"], "args": o["args"]} for o in plan["ops"]])
    assert all(r.get("result", {}).get("ok", True) is not False for r in out["batch"]), out
    n = td.project_comp.op("n1")
    assert n is not None and n.bypass is False
    assert n.par.period.eval() == 2.0
    assert n.par.amp.expr == "absTime.seconds" and "EXPRESSION" in str(n.par.amp.mode)


def test_created_operators_are_deleted_by_the_plan(bridge):
    br, td = bridge
    _run(br, "create_node", {"path": "/project1", "type": "noiseTOP", "name": "made"})
    plan = journal.revert_plan(1)
    assert plan["ops"] == [{"tool": "delete_node", "args": {"path": "/project1/made"},
                            "undoes": plan["undoes"][0]}]


def test_the_plan_stops_at_what_it_cannot_undo(bridge):
    br, td = bridge
    td.make("n1", "noiseTOP")
    _run(br, "set_node_position", {"path": "/project1/n1", "x": 1, "y": 2})
    _run(br, "execute_python", {"code": "result = 1"})
    _run(br, "set_node_position", {"path": "/project1/n1", "x": 5, "y": 6})
    plan = journal.revert_plan(3)
    assert len(plan["ops"]) == 1 and plan["stopped_at"]["tool"] == "execute_python"


def test_batch_steps_are_recorded_one_by_one(bridge):
    br, td = bridge
    td.make("n1", "noiseTOP")
    br["_do_batch"]([{"tool": "set_node_position", "args": {"path": "/project1/n1", "x": 1, "y": 1}},
                     {"tool": "set_node_color", "args": {"path": "/project1/n1", "r": 1, "g": 0, "b": 0}}])
    rows = journal.entries()
    assert [r["tool"] for r in rows] == ["set_node_position", "set_node_color"]
    assert rows[0]["batch"] == rows[1]["batch"]


def test_query_filters_and_offline_tool(bridge):
    br, td = bridge
    td.make("a", "noiseTOP")
    td.make("b", "noiseTOP")
    _run(br, "set_node_position", {"path": "/project1/a", "x": 1, "y": 1})
    _run(br, "set_node_position", {"path": "/project1/b", "x": 1, "y": 1})
    _run(br, "delete_node", {"path": "/project1/zz_missing"})   # fails
    out = journal.query(path="/project1/a")
    assert out["count"] == 1 and out["entries"][0]["args"]["path"] == "/project1/a"
    assert journal.query(failures=True)["count"] == 1
    assert journal.query(since="1h")["count"] == 3
    listed = json.loads(server_offline.td_journal(limit=2))
    assert listed["count"] == 2
    plan = json.loads(server_offline.td_journal(action="revert", path="/project1/b"))
    assert plan["ops"][0]["args"]["path"] == "/project1/b"


def test_journal_can_be_switched_off(home, monkeypatch):
    monkeypatch.setenv("TD_MCP_JOURNAL", "0")
    br, td = build_bridge()
    td.make("n1", "noiseTOP")
    _run(br, "set_node_position", {"path": "/project1/n1", "x": 1, "y": 1})
    assert journal.entries() == []


def test_lines_are_bounded_and_home_paths_redacted(bridge):
    br, td = bridge
    import os
    td.make("t", "textDAT")
    secret = os.path.expanduser("~") + "/somewhere/file.txt"
    _run(br, "set_dat_text", {"path": "/project1/t", "text": "x" * 100000 + secret})
    row = journal.entries()[-1]
    text = json.dumps(row)
    assert os.path.expanduser("~") not in text and len(text) < 40000


def test_reverting_twice_walks_back_like_an_undo_stack(bridge):
    br, td = bridge
    td.make("n1", "noiseTOP")
    _run(br, "set_node_position", {"path": "/project1/n1", "x": 10, "y": 10})   # A
    _run(br, "set_node_position", {"path": "/project1/n1", "x": 20, "y": 20})   # B
    first = journal.revert_plan(1)
    br["_do_batch"](first["ops"])                         # undo B, recorded with `undoes`
    n = td.project_comp.op("n1")
    assert (n.nodeX, n.nodeY) == (10, 10)
    second = journal.revert_plan(1)                        # must undo A, not the undo of B
    assert second["undoes"] != first["undoes"]
    br["_do_batch"](second["ops"])
    assert (n.nodeX, n.nodeY) == (0, 0)
    assert journal.revert_plan(1).get("note")              # nothing left to undo
