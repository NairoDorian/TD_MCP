"""Self-healing build orchestrator (capstone of the self-healing theme).

Combines ``validation`` (TD_Builder 5-stage), ``scoring`` (tdmcp scoreBuild),
``macro``/``repair``, and ``recovery`` (Embody) into one assessable unit:
validate -> score -> auto-repair -> attach recovery hints for anything still
unresolved. Pure, fully testable, and exposed on the offline server as
``td_validate_build`` / ``td_self_heal`` (no running TD required).

Run:  uv run python -m tests.test_heal
"""

from __future__ import annotations

from typing import Any, Dict

from td_mcp.tools.recovery import recovery_hint
from td_mcp.scoring import repair_network, score_build
from td_mcp.validation import suggest_repairs, validate_build


def assess_build(desc: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + score a network and attach recovery hints for each finding."""
    report = validate_build(desc)
    sc = score_build(desc)
    repairs = suggest_repairs(report)
    recovery = []
    for f in report.get("findings", []):
        if f["severity"] == "error":
            recovery.append({
                "target": f["target"], "code": f["code"],
                "recovery": recovery_hint(
                    f"{f['code']}: {f['message']}", {"tool": "build_and_verify"}),
            })
    return {
        "ok": report["ok"],
        "validation": {"error_count": report["error_count"],
                       "warning_count": report["warning_count"],
                       "stages": report["stages"]},
        "score": sc,
        "repairs": repairs,
        "recovery": recovery,
    }


def self_heal(desc: Dict[str, Any], max_iter: int = 4) -> Dict[str, Any]:
    """Iteratively repair and re-assess; attach recovery hints for survivors."""
    fixed, iterations, sc = repair_network(desc, max_iter=max_iter)
    assessment = assess_build(fixed)
    assessment["iterations"] = iterations
    assessment["fixed_desc"] = fixed
    assessment["improved"] = sc["score"] >= score_build(desc)["score"]
    return assessment
