"""Progress reporting (touchdesigner_agent_mcp report_progress).

Long-running builds should emit structured progress so a UI/agent can show
status instead of blocking. This is a tiny pure tracker that produces the
same shape ``report_progress`` would: ``{step, total, percent, label, detail}``.

Run:  uv run python -m tests.test_progress
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class Progress:
    def __init__(self, total: Optional[int] = None, label: str = ""):
        self.total = total
        self.label = label
        self.steps: List[Dict[str, Any]] = []
        self.current = 0

    def step(self, label: str, detail: str = "", advance: int = 1) -> Dict[str, Any]:
        self.current += advance
        rec = {
            "step": self.current,
            "total": self.total,
            "percent": round(100 * self.current / self.total, 1) if self.total else None,
            "label": label,
            "detail": detail,
        }
        self.steps.append(rec)
        return rec

    def report(self) -> Dict[str, Any]:
        last = self.steps[-1] if self.steps else None
        return {
            "current": self.current,
            "total": self.total,
            "percent": last["percent"] if last else None,
            "last": last,
        }

    def done(self, detail: str = "complete") -> Dict[str, Any]:
        return self.step("done", detail=detail)
