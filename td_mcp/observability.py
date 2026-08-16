"""Opt-in observability sink: SQLite trace log of tool-call + LLM spans.

Gate behind ``TD_MCP_OBSERVE`` (a path to a ``.db`` file, or ``"1"`` for the
default ``~/.td_mcp/memory/traces.db``).  When the env var is unset, every
method is a cheap no-op so the core stays dependency-free and allocation-free
in the hot path.

Spans are LLM-style: a name, start/end timestamps, attributes (model, token
counts, tool name, recovery hints used), and a status.  The table is plain
SQLite so a Prometheus-style exporter or Langfuse ingestion script can read it
directly:

    SELECT name, created, duration_ms, attrs FROM td_mcp_spans ORDER BY id DESC

Run:  uv run python -m td_mcp.observability
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from td_mcp.memory import _resolve_path

_DEFAULT_TRACE_PATH = Path.home() / ".td_mcp" / "memory" / "traces.db"


class TraceLog:
    """SQLite span recorder.  No-op (but not erroring) when disabled."""

    def __init__(self, path: Optional[str] = None):
        raw = path or os.environ.get("TD_MCP_OBSERVE")
        if not raw or raw == "0":
            self._enabled = False
            self._db_path = None
            self._conn = None
            return
        self._enabled = True
        if raw == "1":
            self._db_path = _DEFAULT_TRACE_PATH
        else:
            self._db_path = _resolve_path(raw)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _ensure_schema(self) -> None:
        assert self._conn is not None
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS td_mcp_spans (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                span_id   TEXT,
                name      TEXT,
                parent_id TEXT,
                created   REAL,
                duration_ms REAL,
                status    TEXT,
                attrs     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_spans_created ON td_mcp_spans(created);
            CREATE INDEX IF NOT EXISTS idx_spans_name    ON td_mcp_spans(name);
            """
        )
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # -- core span API --------------------------------------------------
    @contextmanager
    def span(self, name: str, parent_id: Optional[str] = None,
             **attrs: Any) -> Iterator[None]:
        """Context manager that records a timed span (no-op when disabled)."""
        if not self._enabled:
            yield
            return
        span_id = str(uuid.uuid4())
        t0 = time.perf_counter()
        status = "ok"
        try:
            yield
        except Exception:
            status = "error"
            raise
        finally:
            dt = (time.perf_counter() - t0) * 1000.0
            assert self._conn is not None
            self._conn.execute(
                "INSERT INTO td_mcp_spans(span_id, name, parent_id, created, "
                "duration_ms, status, attrs) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (span_id, name, parent_id, t0, dt, status,
                 json.dumps(attrs, default=str, ensure_ascii=False)),
            )
            self._conn.commit()

    def tool_span(self, tool_name: str, args_summary: Optional[Dict[str, Any]] = None,
                  result_ok: Optional[bool] = None) -> str:
        """Explicitly record a tool invocation. Returns the span_id."""
        span_id = str(uuid.uuid4())
        if not self._enabled:
            return span_id
        attrs = {"tool": tool_name}
        if args_summary is not None:
            attrs["args"] = args_summary
        if result_ok is not None:
            attrs["ok"] = result_ok
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO td_mcp_spans(span_id, name, created, duration_ms, status, attrs) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            (span_id, "tool_call", time.time(),
             "ok" if result_ok else ("error" if result_ok is False else "ok"),
             json.dumps(attrs, default=str, ensure_ascii=False)),
        )
        self._conn.commit()
        return span_id

    def llm_span(self, model: str, prompt_tokens: int, completion_tokens: int,
                 **attrs: Any) -> str:
        """Record an LLM completion call."""
        span_id = str(uuid.uuid4())
        if not self._enabled:
            return span_id
        a = {"model": model, "prompt_tokens": prompt_tokens,
             "completion_tokens": completion_tokens,
             "total_tokens": prompt_tokens + completion_tokens}
        a.update(attrs)
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO td_mcp_spans(span_id, name, created, duration_ms, status, attrs) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            (span_id, "llm_call", time.time(), "ok",
             json.dumps(a, default=str, ensure_ascii=False)),
        )
        self._conn.commit()
        return span_id

    # -- query ----------------------------------------------------------
    def recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        if not self._enabled or not self._conn:
            return []
        rows = self._conn.execute(
            "SELECT span_id, name, parent_id, created, duration_ms, status, attrs "
            "FROM td_mcp_spans ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_span(r) for r in rows]

    def export(self) -> List[Dict[str, Any]]:
        if not self._enabled or not self._conn:
            return []
        rows = self._conn.execute(
            "SELECT span_id, name, parent_id, created, duration_ms, status, attrs "
            "FROM td_mcp_spans ORDER BY id"
        ).fetchall()
        return [_row_to_span(r) for r in rows]

    def count(self) -> int:
        if not self._enabled or not self._conn:
            return 0
        row = self._conn.execute("SELECT count(*) AS n FROM td_mcp_spans").fetchone()
        return row["n"] if row else 0


def _row_to_span(r: sqlite3.Row) -> Dict[str, Any]:
    out = {
        "span_id": r["span_id"],
        "name": r["name"],
        "parent_id": r["parent_id"],
        "created": r["created"],
        "duration_ms": r["duration_ms"],
        "status": r["status"],
    }
    try:
        out["attrs"] = json.loads(r["attrs"]) if r["attrs"] else {}
    except (ValueError, TypeError):
        out["attrs"] = {}
    return out


# Singleton for the running process.  Auto-detects the env var once.
_default: Optional[TraceLog] = None


def get_trace_log() -> TraceLog:
    global _default
    if _default is None:
        _default = TraceLog()
    return _default


def reset_trace_log() -> None:
    """Discard the cached singleton (used by tests / process teardown)."""
    global _default
    if _default is not None:
        _default.close()
    _default = None


if __name__ == "__main__":
    tl = get_trace_log()
    if not tl.enabled:
        print("observability disabled (set TD_MCP_OBSERVE=1 to enable)")
    else:
        spans = tl.recent(10)
        print(f"{tl.count()} spans logged")
        for s in spans[:10]:
            print(f"  {s['created']:.0f}  {s['name']:12s}  {s.get('attrs', {}).get('tool', s.get('attrs', {}).get('model', ''))}  {s['status']}")
        tl.close()
