"""Session memory (tdmcp Obsidian vault / AI session memory, Embody).

A lightweight, local-first memory of the agent's interactions with a TD
project: saves turns (role + text + tags), recalls the most relevant past
entries by keyword overlap, and can summarize recent context. Used to give an
agent continuity across sessions without an external vector DB. Pure file I/O,
unit-testable.

Run:  uv run python -m tests.test_memory
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class SessionMemory:
    def __init__(self, path: Optional[str] = None):
        self.path = Path(path or (Path.home() / ".td_mcp" /
                                   "memory" / "session.jsonl"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        self._entries.append(json.loads(line))
            except Exception:
                self._entries = []

    def _persist(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for e in self._entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    def save(self, role: str, text: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
        entry = {"ts": time.time(), "role": role, "text": text,
                 "tags": tags or []}
        self._entries.append(entry)
        self._persist()
        return entry

    def recall(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        """Rank past entries by keyword overlap with ``query``."""
        q = set(query.lower().split())
        scored = []
        for e in self._entries:
            text = (e.get("text", "") + " " + " ".join(e.get("tags", []))).lower()
            score = sum(1 for w in q if w and w in text.split())
            if score > 0:
                scored.append((score, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:k]]

    def summarize(self, last: int = 10) -> str:
        recent = self._entries[-last:]
        lines = [f"[{e['role']}] {e['text'][:160]}" for e in recent]
        return "\n".join(lines) if lines else "(no memory yet)"

    def __len__(self) -> int:
        return len(self._entries)
