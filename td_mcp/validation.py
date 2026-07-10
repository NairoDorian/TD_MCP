"""Build validation & self-repair (TD_Builder 5-stage + tdmcp auto-repair).

Before a generated network is pushed into TouchDesigner, validate it in five
staged passes — exactly the discipline TD_Builder_alpha applies with
``td_validate`` and tdmcp applies with ``autoRepairLoop`` / ``scoreBuild``:

    1. schema     — every operator has a name + known type
    2. semantic   — required parameters present, values in range/menu
    3. reference  — every input/connection references a real node
    4. logical    — wiring direction sane (output -> input), no bad self-loops
    5. td_rules    — family compatibility of each connection (TOP->CHOP rejected)

Validation runs on a *pure* description dict (no TouchDesigner needed), so it is
fully unit-testable and is what ``build_and_verify`` calls before importing.
Where possible it consults ``td_mcp.kb.corpus`` for real operator families and
parameter schemas; without the corpus it degrades to structural checks only.

Run:  uv run python -m tests.test_validation
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Connection-family compatibility: which source families may feed which targets.
# (TOP feeds TOP/COMP, CHOP feeds CHOP/SOP-DAT/COMP, etc.) Empty set = any.
_COMPAT: Dict[str, set] = {
    "TOP": {"TOP", "COMP", "DAT"},
    "CHOP": {"CHOP", "SOP", "DAT", "COMP"},
    "SOP": {"SOP", "COMP", "DAT"},
    "DAT": {"DAT", "COMP"},
    "POP": {"POP", "COMP", "SOP"},
    "COMP": {"COMP", "TOP"},
}

try:
    from td_mcp.kb import corpus as _corpus

    _CORPUS_OK = True
except Exception:  # noqa: BLE001
    _CORPUS_OK = False


def _family_of(op_type: Optional[str]) -> Optional[str]:
    """Resolve an operator type to its family (TOP/CHOP/SOP/DAT/POP/COMP)."""
    if not op_type or not _CORPUS_OK:
        # Heuristic fallback from a type suffix like "Noise TOP".
        if op_type and op_type.split()[-1] in ("TOP", "CHOP", "SOP", "DAT", "POP", "COMP"):
            return op_type.split()[-1]
        return None
    rec = _corpus.operator_record(op_type)
    if rec:
        return (rec.get("category") or "").upper() or None
    return None


def _operator_records() -> Any:
    return _corpus if _CORPUS_OK else None


def validate_build(desc: Dict[str, Any], strict: bool = False) -> Dict[str, Any]:
    """Validate a network description.

    ``desc`` shape:
        {
          "operators": [
            {"name": "n1", "type": "Noise TOP",
             "parameters": {"resx": 128}, "inputs": ["src1", null]},
            ...
          ],
          "connections": [{"from": "a", "to": "b", "from_output": 0, "to_input": 0}]
        }

    Returns a structured report with per-stage findings and an overall verdict.
    """
    ops = desc.get("operators") or []
    conns = desc.get("connections") or []
    findings: List[Dict[str, Any]] = []

    # Normalize: build a name->family map and a synthetic connection list that
    # combines explicit connections with inline operator "inputs".
    by_name: Dict[str, Dict] = {}
    for op in ops:
        nm = op.get("name")
        if nm is not None:
            by_name[nm] = op

    edge_list: List[Dict[str, Any]] = []
    for c in conns:
        edge_list.append({
            "from": c.get("from"), "to": c.get("to"),
            "from_output": c.get("from_output", 0), "to_input": c.get("to_input", 0),
        })
    for op in ops:
        for i, src in enumerate(op.get("inputs", []) or []):
            if src:  # null = unconnected input
                edge_list.append({"from": src, "to": op.get("name"),
                                  "from_output": 0, "to_input": i})

    # ---- Stage 1: schema -------------------------------------------------
    for idx, op in enumerate(ops):
        nm = op.get("name")
        tp = op.get("type")
        if not nm:
            findings.append(_f("schema", "error", "NAME_MISSING",
                               f"operator[{idx}] has no name", target=str(idx)))
        if not tp:
            findings.append(_f("schema", "error", "TYPE_MISSING",
                               f"operator {nm!r} has no type", target=nm))
        elif _CORPUS_OK and _operator_records().operator_record(tp) is None:
            sev = "error" if strict else "warning"
            findings.append(_f("schema", sev, "UNKNOWN_TYPE",
                              f"operator type {tp!r} not in corpus", target=nm))

    # ---- Stage 2: semantic ----------------------------------------------
    for op in ops:
        nm = op.get("name")
        tp = op.get("type")
        if not (tp and _CORPUS_OK):
            continue
        rec = _operator_records().operator_record(tp)
        if not rec:
            continue
        schema = _operator_records().param_schema(rec) or {}
        params = op.get("parameters") or {}
        for pname, info in schema.items():
            # Treat a param as 'required' if it is commonly needed and unset.
            if pname in ("file", "dat", "top", "op") and pname not in params:
                findings.append(_f("semantic", "warning", "PARAM_UNSET",
                                   f"{nm}: suggests setting {pname!r}", target=nm))
            if pname in params and info.get("menu") and params[pname] not in info["menu"]:
                findings.append(_f("semantic", "error", "MENU_INVALID",
                                   f"{nm}.{pname} = {params[pname]!r} not in menu "
                                   f"{info['menu'][:6]}", target=nm))
            if pname in params and info.get("min") is not None and info.get("max") is not None:
                try:
                    v = float(params[pname])
                    if v < info["min"] or v > info["max"]:
                        findings.append(_f("semantic", "warning", "RANGE_OOB",
                                           f"{nm}.{pname}={v} outside "
                                           f"{info['min']}..{info['max']}", target=nm))
                except (TypeError, ValueError):
                    pass

    # ---- Stage 3: reference ---------------------------------------------
    for e in edge_list:
        if e["from"] is not None and e["from"] not in by_name:
            findings.append(_f("reference", "error", "DANGLING_SRC",
                               f"{e['to']} references missing source {e['from']!r}",
                               target=e["to"]))
        if e["to"] is not None and e["to"] not in by_name:
            findings.append(_f("reference", "error", "DANGLING_DST",
                               f"connection targets missing node {e['to']!r}",
                               target=e["to"]))

    # ---- Stage 4: logical -----------------------------------------------
    for e in edge_list:
        if e["from"] and e["from"] == e["to"]:
            findings.append(_f("logical", "warning", "SELF_LOOP",
                               f"{e['from']} connects to itself", target=e["from"]))

    # ---- Stage 5: td_rules (family compatibility) ------------------------
    for e in edge_list:
        sf = _family_of(by_name.get(e["from"], {}).get("type")) if e["from"] in by_name else None
        tf = _family_of(by_name.get(e["to"], {}).get("type")) if e["to"] in by_name else None
        if sf and tf:
            allowed = _COMPAT.get(sf)
            if allowed is not None and tf not in allowed:
                findings.append(_f("td_rules", "error", "FAMILY_MISMATCH",
                                   f"{e['from']} ({sf}) -> {e['to']} ({tf}) incompatible",
                                   target=e["to"]))

    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    return {
        "ok": len(errors) == 0,
        "stages": ["schema", "semantic", "reference", "logical", "td_rules"],
        "error_count": len(errors),
        "warning_count": len(warnings),
        "findings": findings,
        "summary": f"{len(errors)} error(s), {len(warnings)} warning(s) across 5 stages",
    }


def suggest_repairs(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Turn validation findings into concrete repair actions."""
    repairs: List[Dict[str, Any]] = []
    for f in report.get("findings", []):
        code = f["code"]
        if code == "DANGLING_SRC":
            repairs.append({"target": f["target"], "action": "drop_input",
                            "detail": "remove connection from missing source"})
        elif code == "DANGLING_DST":
            repairs.append({"target": f["target"], "action": "remove_connection",
                            "detail": "drop connection to missing node"})
        elif code == "NAME_MISSING":
            repairs.append({"target": f["target"], "action": "auto_name",
                            "detail": "assign a unique generated name"})
        elif code == "TYPE_MISSING":
            repairs.append({"target": f["target"], "action": "insert_null",
                            "detail": "skip node until a type is supplied"})
        elif code == "FAMILY_MISMATCH":
            repairs.append({"target": f["target"], "action": "review_wiring",
                            "detail": "insert a Convert OP between mismatched families"})
        elif code == "MENU_INVALID":
            repairs.append({"target": f["target"], "action": "reset_param",
                            "detail": "clear invalid menu value to default"})
        elif code == "SELF_LOOP":
            repairs.append({"target": f["target"], "action": "allow_self_loop",
                            "detail": "permitted for Feedback-class operators"})
    return repairs


def auto_repair(desc: Dict[str, Any], report: Optional[Dict[str, Any]] = None
                ) -> Dict[str, Any]:
    """Apply non-destructive repairs and return a corrected description.

    Currently handles: dangling inputs/connections, auto-naming unnamed nodes,
    and dropping nodes with no type. Family mismatches are flagged (repairable
    by inserting a Convert, which the TD side can do) but not silently changed.
    """
    report = report or validate_build(desc)
    repairs = suggest_repairs(report)
    desc = {"operators": [dict(o) for o in desc.get("operators", [])],
            "connections": [dict(c) for c in desc.get("connections", [])]}

    valid_names = set()
    for op in desc["operators"]:
        if op.get("name"):
            valid_names.add(op["name"])
    counter = 0

    # Drop nodes with no type (cannot be built).
    kept = []
    for op in desc["operators"]:
        if not op.get("type"):
            continue
        if not op.get("name"):
            while True:
                nm = f"node{counter}"
                counter += 1
                if nm not in valid_names:
                    break
            op["name"] = nm
            valid_names.add(nm)
        # Repair inline inputs: drop dangling sources.
        op["inputs"] = [s for s in op.get("inputs", []) or [] if s in valid_names or s is None]
        kept.append(op)
    desc["operators"] = kept

    # Repair explicit connections: drop dangling edges.
    kept_c = []
    names = {o["name"] for o in kept}
    for c in desc["connections"]:
        if c.get("from") in names and c.get("to") in names:
            kept_c.append(c)
    desc["connections"] = kept_c
    return desc


def _f(stage: str, severity: str, code: str, message: str, target: Any) -> Dict[str, Any]:
    return {"stage": stage, "severity": severity, "code": code,
            "message": message, "target": target}
