"""Tests for activity log, history recall, and per-operator version compat."""

import json

from td_mcp.activity import ActivityLog, get_activity_log, history_recall
from td_mcp.kb import corpus
from td_mcp.memory import pre_turn_injection
from td_mcp.server_offline import td_activity_log, td_history_recall, td_docs_version_compat


def test_activity_log_ring_buffer():
    lg = ActivityLog(max_entries=3)
    for i in range(5):
        lg.add(f"tool{i}", result_summary=f"result {i}")
    assert len(lg) == 3
    entries = lg.list()
    assert entries[0]["tool"] == "tool2"
    assert entries[-1]["tool"] == "tool4"


def test_activity_log_tsv():
    lg = ActivityLog()
    lg.add("td_docs_search", result_summary="hit")
    assert "td_docs_search" in lg.to_tsv()


def test_global_activity_log():
    lg = get_activity_log()
    lg.add("td_docs_search")
    assert len(lg) >= 1


def test_td_activity_log_tool():
    get_activity_log().add("td_memory_save")
    out = td_activity_log(10)
    data = json.loads(out)
    assert isinstance(data, list)


def test_td_history_recall_tool():
    out = td_history_recall("feedback")
    data = json.loads(out)
    assert isinstance(data, list)


def test_history_recall_session(tmp_path):
    from td_mcp.memory import SessionMemory
    import os
    os.environ["TD_MCP_MEMORY_DB"] = str(tmp_path / "mem.db")
    m = SessionMemory()
    m.save("user", "create a feedback TOP network", tags=["feedback"])
    hits = history_recall("feedback", k=3)
    assert any(h["source"] == "session" for h in hits)


def test_pre_turn_injection_empty():
    assert pre_turn_injection("") == ""


def test_pre_turn_injection_returns_something(tmp_path):
    import os
    from td_mcp.memory import SessionMemory
    os.environ["TD_MCP_MEMORY_DB"] = str(tmp_path / "mem2.db")
    m = SessionMemory()
    m.save("user", "feedback loop network")
    text = pre_turn_injection("feedback")
    assert "feedback" in text.lower()


def test_operator_version_compat_known():
    rec = corpus.operator_version_compat("Blur TOP")
    assert rec["ok"]
    assert rec["name"] == "Blur TOP"
    assert "compatible" in rec


def test_operator_version_compat_with_build():
    rec = corpus.operator_version_compat("Blur TOP", build="2022")
    assert rec["compatible"] in (True, False)


def test_td_docs_version_compat_tool():
    out = td_docs_version_compat("Noise CHOP")
    data = json.loads(out)
    assert data["ok"]
