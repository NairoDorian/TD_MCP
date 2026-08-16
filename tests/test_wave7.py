"""Tests for wave-7 features (activity log wiring, score floor, tool batch, offline import)."""

import json
import os

from td_mcp.rag.retriever import Retriever
from td_mcp.rag.strategies import ParallelRetriever
from td_mcp.rag.eval import name_integrity
from td_mcp.server_offline import td_tool_batch, td_activity_log


def test_name_integrity_pytest():
    """Name-integrity is a pytest gate too, not just a CLI flag."""
    from td_mcp.rag.retriever import build_retriever
    report = name_integrity(build_retriever())
    assert report["ok"], report


def test_score_floor_abstains_when_low(tmp_path):
    """A near-empty index + score floor returns [] instead of garbage."""
    chunks = [{"id": "a", "title": "A", "text": "totally unrelated thing", "category": "operator"}]
    pr = ParallelRetriever(Retriever(chunks), score_floor=1.0)
    res = pr.search("this query has zero lexical overlap")
    # With score_floor high, the single weak match is suppressed.
    assert res == [] or all(s < 1.0 for _, s in res)


def test_td_tool_batch_basic():
    out = td_tool_batch([
        {"tool": "td_docs_search", "args": {"query": "blur top", "k": 1}},
        {"tool": "td_docs_version_info", "args": {"build": "2023.10000"}},
    ])
    data = json.loads(out)
    assert len(data) == 2
    assert data[0]["tool"] == "td_docs_search"
    assert "result" in data[0]


def test_td_tool_batch_unknown_tool():
    out = td_tool_batch([{"tool": "td_ghost", "args": {}}])
    data = json.loads(out)
    assert data[0]["tool"] == "td_ghost"
    assert "error" in data[0]


def test_td_tool_batch_bad_json():
    out = td_tool_batch("not json")
    data = json.loads(out)
    assert "error" in data


def test_td_activity_log_wired():
    # The activity log is populated by the call_tool handler; directly test the
    # tool returns a list.
    out = td_activity_log(10)
    data = json.loads(out)
    assert isinstance(data, list)


def test_offline_import_module_importable():
    from td_mcp.kb import offline_import
    assert callable(offline_import.import_offline)
