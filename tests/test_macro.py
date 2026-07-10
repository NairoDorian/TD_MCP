"""Tests for td_mcp.macro (record / replay / dedupe)."""

from td_mcp.macro import MacroRecorder


def _disp(tool, args):
    return {"tool": tool, "args": args, "ok": True}


def test_record_and_ops():
    m = MacroRecorder("m1")
    m.record("create_node", {"path": "/p", "type": "Noise TOP"}, {"ok": True})
    m.record("connect_nodes", {"from_path": "a", "to_path": "b"}, {"ok": True})
    ops = m.as_ops()
    assert len(ops) == 2
    assert ops[0]["tool"] == "create_node"


def test_replay_dispatches():
    m = MacroRecorder()
    m.record("create_node", {"type": "Noise TOP"}, {"ok": True})
    called = []
    m.replay(lambda t, a: called.append((t, a)))
    assert called == [("create_node", {"type": "Noise TOP"})]


def test_dedupe_removes_repeats():
    m = MacroRecorder()
    m.record("set_parameters", {"path": "x", "params": {"a": 1}})
    m.record("set_parameters", {"path": "x", "params": {"a": 1}})
    removed = m.dedupe()
    assert removed == 1
    assert len(m.entries) == 1


def test_serialize_roundtrip():
    m = MacroRecorder("orig")
    m.record("create_node", {"type": "Level TOP"}, {"ok": True})
    text = m.serialize()
    m2 = MacroRecorder.deserialize(text)
    assert m2.name == "orig"
    assert m2.entries[0]["tool"] == "create_node"
