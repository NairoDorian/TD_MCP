"""Tests for td_mcp.observability (TraceLog SQLite span recorder)."""

import os

from td_mcp.observability import TraceLog, get_trace_log, reset_trace_log


def test_disabled_by_default(tmp_path):
    os.environ.pop("TD_MCP_OBSERVE", None)
    tl = TraceLog()
    assert tl.enabled is False
    assert tl.count() == 0
    # span + tool_span + llm_span are all no-ops
    with tl.span("tool_call", tool="test"):
        pass
    tl.tool_span("td_docs_search", result_ok=True)
    tl.llm_span("gpt-4", 10, 5)
    assert tl.count() == 0


def test_span_records_timing(tmp_path):
    db = str(tmp_path / "traces.db")
    tl = TraceLog(db)
    assert tl.enabled
    with tl.span("tool_call", tool="td_docs_search"):
        pass
    spans = tl.recent(10)
    assert len(spans) == 1
    s = spans[0]
    assert s["name"] == "tool_call"
    assert s["status"] == "ok"
    assert s["duration_ms"] >= 0.0
    assert s["attrs"]["tool"] == "td_docs_search"


def test_span_error_status(tmp_path):
    db = str(tmp_path / "t.db")
    tl = TraceLog(db)
    try:
        with tl.span("tool_call", tool="broken"):
            raise ValueError("boom")
    except ValueError:
        pass
    spans = tl.recent(10)
    assert len(spans) == 1
    assert spans[0]["status"] == "error"


def test_tool_span_and_llm_span(tmp_path):
    db = str(tmp_path / "t.db")
    tl = TraceLog(db)
    sid = tl.tool_span("td_memory_save", args_summary={"role": "user"}, result_ok=True)
    lid = tl.llm_span("gpt-4o", 100, 50, temperature=0.3)
    assert sid and lid
    spans = tl.recent(10)
    by_name = {s["name"]: s for s in spans}
    assert "tool_call" in by_name
    assert "llm_call" in by_name
    assert by_name["llm_call"]["attrs"]["total_tokens"] == 150


def test_export_and_clear(tmp_path):
    db = str(tmp_path / "t.db")
    tl = TraceLog(db)
    with tl.span("x"):
        pass
    assert tl.count() == 1
    exported = tl.export()
    assert len(exported) == 1
    assert exported[0]["name"] == "x"
