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

#: Recovery for validation findings, keyed by code. The generic error lookup
#: these used to go through answered MENU_INVALID with "Unrecognized error from
#: the bridge or TouchDesigner" -- found by driving td_validate_build over MCP
#: as an agent would (scripts/live_smoke.py), not by any unit test.
_FINDING_HINTS = {
    "MENU_INVALID": ("A menu parameter has a value TouchDesigner does not accept "
                     "(TD would silently keep the old value).",
                     "Use one of the tokens listed in the message, or let td_self_heal "
                     "reset it to the creation default.",
                     ["td_describe_op_type", "td_self_heal"]),
    "PARAM_UNKNOWN": ("The operator has no parameter by that name.",
                      "Look the exact name up (the message suggests close matches).",
                      ["td_describe_op_type", "td_docs_parameter"]),
    "PARAM_RENAMED": ("A friendly or abbreviated parameter name was mapped to the real one.",
                      "Use the real name, or run td_self_heal to rename it.",
                      ["td_self_heal"]),
    "PARAM_READ_ONLY": ("The parameter is read-only; a write would be refused.",
                        "Remove it from the spec.", ["td_describe_op_type"]),
    "PARAM_UNSET": ("A reference the operator needs (file / top / chop ...) is empty.",
                    "Set it to the operator or file it should read.", ["td_describe_op_type"]),
    "UNKNOWN_TYPE": ("The operator type does not exist in this TouchDesigner build.",
                     "Use a TD class name (noiseTOP, lfoCHOP, gridPOP ...).",
                     ["td_docs_family", "td_describe_op_type"]),
    "AMBIGUOUS_TYPE": ("A short type name exists in several families.",
                       "Use the full opType (noiseTOP vs noiseCHOP).", ["td_describe_op_type"]),
    "FAMILY_MISMATCH": ("Two operators of incompatible families are wired together.",
                        "Insert a converter (e.g. CHOP to TOP / TOP to CHOP) or rewire.",
                        ["td_docs_connections"]),
    "DANGLING_SRC": ("An input names an operator that is not in the spec.",
                     "Add the source operator or fix the name; td_self_heal drops the wire.",
                     ["td_self_heal"]),
    "DANGLING_DST": ("A connection targets an operator that is not in the spec.",
                     "Add the operator or fix the name; td_self_heal drops the wire.",
                     ["td_self_heal"]),
    "NAME_MISSING": ("An operator has no name.", "Name it; td_self_heal auto-names it.",
                     ["td_self_heal"]),
    "TYPE_MISSING": ("An operator has no type.", "Give it a TD class name.",
                     ["td_docs_family"]),
}


def _finding_hint(f: Dict[str, Any]) -> Dict[str, Any]:
    known = _FINDING_HINTS.get(f.get("code"))
    if known:
        cause, action, tools = known
        return {"cause": cause, "action": action, "next_tools": tools}
    return recovery_hint(f"{f['code']}: {f['message']}", {"tool": "build_and_verify"})


def assess_build(desc: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + score a network and attach recovery hints for each finding."""
    report = validate_build(desc)
    sc = score_build(desc)
    repairs = suggest_repairs(report)
    recovery = []
    for f in report.get("findings", []):
        if f["severity"] == "error":
            recovery.append({"target": f["target"], "code": f["code"],
                             "recovery": _finding_hint(f)})
    return {
        "ok": report["ok"],
        # The findings ARE the answer: which operator, which parameter, which
        # valid values. Counts alone left an agent knowing only that it failed.
        "validation": {"error_count": report["error_count"],
                       "warning_count": report["warning_count"],
                       "stages": report["stages"],
                       "findings": [{k: f[k] for k in ("severity", "code", "target", "message")}
                                    for f in report.get("findings", [])]},
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
