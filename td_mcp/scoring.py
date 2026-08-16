"""Build scoring & self-repair loop (tdmcp scoreBuild / repairNetwork).

``td_mcp.validation`` decides *whether* a network is valid. This module adds the
agent's *quality* judgment: a numeric ``scoreBuild`` (0..100) rewarding complete,
well-formed, corpus-backed networks and penalizing findings, plus a
``repair_network`` loop that iteratively applies ``auto_repair`` until the build
is clean or a cap is hit (tdmcp's ``autoRepairLoop``).

Run:  uv run python -m tests.test_scoring
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from td_mcp.validation import auto_repair, validate_build


def score_build(desc: Dict[str, Any]) -> Dict[str, Any]:
    """Score a network description 0..100.

    Rewards: valid (no errors), all nodes typed, complete connections (no
    dangling), use of known operator types, and a reasonable node count.
    Penalizes: each error/warning, typeless nodes, dangling edges.

    If ``desc`` carries a ``viewport_verdict`` (from ``vision.viewport_verdict``),
    a black/flat/transparent render is penalized — a network that renders nothing
    is almost certainly mis-wired regardless of structural validity.
    """
    report = validate_build(desc)
    ops = desc.get("operators") or []
    total = max(1, len(ops))

    score = 100
    score -= 12 * report["error_count"]
    score -= 4 * report["warning_count"]

    typed = sum(1 for o in ops if o.get("type"))
    score -= 10 * (total - typed) / total
    # Connectivity: fraction of non-first nodes that have at least one input.
    wired = 0
    for o in ops:
        ins = o.get("inputs") or []
        if any(i for i in ins):
            wired += 1
    if total > 1:
        score -= 15 * (1 - wired / max(1, total - 1))

    # Viewport verdict penalties (tdmcp captionTop idea).
    vv = desc.get("viewport_verdict") or {}
    viewport_penalty = 0
    viewport_flags = []
    if vv.get("is_black"):
        viewport_penalty += 20
        viewport_flags.append("black")
    if vv.get("is_flat"):
        viewport_penalty += 10
        viewport_flags.append("flat")
    if vv.get("fully_transparent"):
        viewport_penalty += 15
        viewport_flags.append("transparent")
    score -= viewport_penalty
    if viewport_flags:
        vv_caption = vv.get("caption", "")
        if vv_caption:
            viewport_flags.append(f"caption: {vv_caption}")

    score = max(0, min(100, round(score)))
    grade = ("A" if score >= 90 else "B" if score >= 75 else
             "C" if score >= 60 else "D" if score >= 40 else "F")
    return {
        "score": score,
        "grade": grade,
        "ok": report["ok"],
        "error_count": report["error_count"],
        "warning_count": report["warning_count"],
        "node_count": total,
        "typed_count": typed,
        "wired_count": wired,
        "viewport_flags": viewport_flags,
        "summary": f"score {score} ({grade}); {report['summary']}"
        + (f"; viewport: {', '.join(viewport_flags)}" if viewport_flags else ""),
    }


def repair_network(desc: Dict[str, Any], max_iter: int = 4
                   ) -> Tuple[Dict[str, Any], int, Dict[str, Any]]:
    """Iteratively auto-repair a network until valid or capped.

    Returns (fixed_desc, iterations_used, final_score).
    """
    current = desc
    iterations = 0
    for _ in range(max_iter):
        report = validate_build(current)
        if report["ok"]:
            break
        current = auto_repair(current, report)
        iterations += 1
        # If a pass produced no change, stop to avoid loops.
        if validate_build(current)["ok"]:
            break
    return current, iterations, score_build(current)
