"""Activity log + history recall (TDPilot technique, MIT-safe reimpl).

A small in-memory ring buffer of recent tool calls with per-call summaries,
plus a history-recall helper that searches the session memory and technique
stores for anything matching a query. Pure stdlib; no observability dependency.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class ActivityLog:
    """200-entry ring buffer of tool-call summaries (TDPilot activity_log)."""

    def __init__(self, max_entries: int = 200):
        self.max_entries = max_entries
        self._entries: List[Dict[str, Any]] = []

    def add(self, tool: str, args_summary: str = "", result_summary: str = "",
            duration_ms: float = 0.0, ok: bool = True) -> None:
        entry = {
            "tool": tool,
            "args_summary": args_summary[:200],
            "result_summary": result_summary[:200],
            "duration_ms": round(duration_ms, 2),
            "ok": ok,
        }
        self._entries.append(entry)
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries:]

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        return list(self._entries[-limit:])

    def to_tsv(self, limit: int = 50) -> str:
        lines = ["ts\ttool\targs_summary\tduration_ms\tok"]
        for e in self.list(limit):
            lines.append("\t".join([
                "", e["tool"], e["args_summary"],
                str(e["duration_ms"]), str(e["ok"]),
            ]))
        return "\n".join(lines)

    def __len__(self):
        return len(self._entries)


_ACTIVITY_LOG = ActivityLog()


def get_activity_log():
    return _ACTIVITY_LOG


def history_recall(query: str, k: int = 5) -> List[Dict[str, Any]]:
    """Recall matching entries across session memory and technique store.

    Returns a combined list of hits tagged with their source
    (``session`` or ``technique``), ranked by keyword overlap.
    """
    from td_mcp.memory import SessionMemory, TechniqueStore

    hits: List[Dict[str, Any]] = []
    try:
        for h in SessionMemory().recall(query, k=k):
            hits.append({"source": "session", "trust_tier": h.get("trust_tier"),
                         "role": h.get("role"), "text": h.get("text"),
                         "ts": h.get("ts")})
    except Exception:  # noqa: BLE001
        pass
    try:
        q_terms = set(query.lower().split())
        for t in TechniqueStore().list(limit=200):
            text = " ".join([t.get("name", ""), t.get("description", ""),
                             " ".join(json_loads(t.get("tags")) if t.get("tags") else [])]).lower()
            overlap = len(q_terms & set(text.split()))
            if overlap:
                hits.append({"source": "technique", "id": t.get("id"),
                             "name": t.get("name"), "state": t.get("state"),
                             "description": t.get("description"),
                             "score": overlap})
    except Exception:  # noqa: BLE001
        pass

    if "technique" in {h.get("source") for h in hits}:
        hits.sort(key=lambda h: -(h.get("score", 0)))
    return hits[:k]


def json_loads(s):
    import json
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return []
