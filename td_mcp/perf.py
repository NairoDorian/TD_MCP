"""Performance analyzer (TD-Codex performance_analyzer).

Given a performance snapshot from the bridge (``get_performance``), classify
the frame rate, rank the slowest-cooking operators, and emit concrete
optimization suggestions. Pure, so it is unit-testable without a running TD.

Run:  uv run python -m tests.test_perf
"""

from __future__ import annotations

from typing import Any, Dict, List


def analyze_performance(perf: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze a performance report.

    Expected ``perf`` shape:
        {"fps": 60, "nodes": [{"name": "/project1/n1", "cook_time": 2.1,
                                "cpu": 80, "gpu": 5}, ...]}
    """
    fps = perf.get("fps")
    raw = perf.get("nodes") or perf.get("cooks") or []
    nodes = [
        {
            "name": n.get("name") or n.get("path"),
            "cook_time": n.get("cook_time"),
            "cpu": n.get("cpu"),
            "gpu": n.get("gpu"),
        }
        for n in raw
    ]

    slow = sorted(nodes, key=lambda n: float(n.get("cook_time", 0) or 0),
                  reverse=True)
    slowest = [
        {"name": n.get("name"), "cook_time": n.get("cook_time"),
         "cpu": n.get("cpu"), "gpu": n.get("gpu")}
        for n in slow[:5]
    ]

    suggestions: List[str] = []
    if fps is not None and fps < 30:
        suggestions.append(
            f"Frame rate is low ({fps} fps). Target >= 30 fps for interactive work.")
    if slowest and (slowest[0].get("cook_time") or 0) > 5.0:
        suggestions.append(
            f"Operator {slowest[0]['name']} cooks slowly "
            f"({slowest[0]['cook_time']} ms) — cache its output or simplify.")
    heavy_cpu = [n for n in nodes if (n.get("cpu") or 0) > 80]
    if heavy_cpu:
        suggestions.append(
            f"{len(heavy_cpu)} operator(s) saturate CPU — move work to GPU TOPs "
            "or reduce resolution/quality.")
    heavy_gpu = [n for n in nodes if (n.get("gpu") or 0) > 80]
    if heavy_gpu:
        suggestions.append(
            "High GPU load — lower render resolution or pixel format.")
    if not suggestions:
        suggestions.append("Performance looks healthy.")

    return {
        "fps": fps,
        "fps_ok": (fps is None) or fps >= 30,
        "slowest": slowest,
        "suggestions": suggestions,
    }
