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
        kept.append(op)

    # Only surviving (typed) nodes can be referenced; rebuild the valid-name
    # set from the survivors so inline-input references to dropped nodes are
    # also removed (avoids DANGLING_SRC on the repaired description).
    kept_names = {o["name"] for o in kept}
    for op in kept:
        # Repair inline inputs: drop dangling sources.
        op["inputs"] = [s for s in op.get("inputs", []) or [] if s in kept_names or s is None]
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


# Parameters whose value is a filesystem path worth existence-checking.
_FILE_PARAM_HINTS = ("file", "path", "moviefile", "audiofile", "image", "folder", "url")


def analyze_build(desc: Dict[str, Any]) -> Dict[str, Any]:
    """Dead-weight / liveness analysis of a network description (tdmcp idea).

    Extends :func:`validate_build` (which checks *validity*) with *usage*:

      * ``isolated``   — a node with no connections at all (pure dead weight)
      * ``no_output``  — a node that is only fed, never consumed (likely unused)
      * ``empty_comp`` — a COMP with zero connections (possible empty container)
      * ``broken_file_dep`` — a file/path parameter pointing at a missing file

    Runs on the pure description dict; file checks are best-effort (a missing
    path is reported, but relative project paths may legitimately not exist yet).

    Returns a structured report with per-category findings.
    """
    ops = desc.get("operators") or []
    conns = desc.get("connections") or []

    by_name: Dict[str, Dict] = {}
    for op in ops:
        nm = op.get("name")
        if nm is not None:
            by_name[nm] = op

    # Build edge lists (explicit + inline inputs).
    outgoing: Dict[str, int] = {nm: 0 for nm in by_name}
    incoming: Dict[str, int] = {nm: 0 for nm in by_name}
    edges = []
    for c in conns:
        f, t = c.get("from"), c.get("to")
        edges.append((f, t))
        if f in outgoing:
            outgoing[f] += 1
        if t in incoming:
            incoming[t] += 1
    for op in ops:
        nm = op.get("name")
        for src in op.get("inputs", []) or []:
            if src:
                edges.append((src, nm))
                if src in outgoing:
                    outgoing[src] += 1
                if nm in incoming:
                    incoming[nm] += 1

    findings: List[Dict[str, Any]] = []
    for op in ops:
        nm = op.get("name", "<unnamed>")
        fam = _family_of(op.get("type")) if op.get("type") else None
        out_n = outgoing.get(nm, 0)
        in_n = incoming.get(nm, 0)

        if out_n == 0 and in_n == 0:
            findings.append(_f("usage", "warning", "ISOLATED",
                               f"{nm} has no connections (dead weight)", target=nm))
        elif out_n == 0:
            findings.append(_f("usage", "info", "NO_OUTPUT",
                               f"{nm} is only fed, never consumed (likely unused)",
                               target=nm))
        if fam == "COMP" and out_n == 0 and in_n == 0:
            findings.append(_f("usage", "warning", "EMPTY_COMP",
                               f"{nm} is a COMP with no connections (possible empty container)",
                               target=nm))

        # Broken file dependencies.
        for pname, pval in (op.get("parameters") or {}).items():
            if not isinstance(pval, str) or not pval:
                continue
            low = pname.lower()
            if any(h in low for h in _FILE_PARAM_HINTS) and _looks_like_path(pval):
                import os as _os
                if not _os.path.exists(pval):
                    findings.append(_f("usage", "warning", "BROKEN_FILE_DEP",
                                       f"{nm}.{pname} -> {pval!r} does not exist",
                                       target=nm))

    cats = {}
    for f in findings:
        cats.setdefault(f["code"], []).append(f["target"])
    return {
        "ok": True,
        "operator_count": len(ops),
        "connection_count": len(conns),
        "categories": cats,
        "finding_count": len(findings),
        "findings": findings,
        "summary": f"{len(findings)} usage finding(s) across {len(ops)} operator(s)",
    }


def _looks_like_path(value: str) -> bool:
    v = value.strip()
    if v.startswith(("http://", "https://", "td://", "$")):
        return False
    return ("/" in v or "\\" in v or v.lower().endswith(
        (".toe", ".tox", ".png", ".jpg", ".jpeg", ".mov", ".mp4", ".wav",
         ".aif", ".aiff", ".mp3", ".txt", ".dat", ".bmp", ".exr", ".tif", ".tiff")))


def diff_networks(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Structural A/B diff of two network descriptions (twozero / TD_Builder idea).

    Compares operators (by name), their parameters, and connections (by
    ``from -> to``). Useful for "what changed between build v1 and v2".

    Returns added / removed / changed operators, parameter deltas, and a
    connection delta, each as a concrete, machine-readable list.
    """
    a_ops = {o.get("name"): o for o in (a.get("operators") or []) if o.get("name")}
    b_ops = {o.get("name"): o for o in (b.get("operators") or []) if o.get("name")}

    added = [n for n in b_ops if n not in a_ops]
    removed = [n for n in a_ops if n not in b_ops]

    changed = []
    for n in a_ops:
        if n not in b_ops:
            continue
        pa = a_ops[n].get("parameters") or {}
        pb = b_ops[n].get("parameters") or {}
        param_deltas = {}
        for k in set(pa) | set(pb):
            va, vb = pa.get(k), pb.get(k)
            if va != vb:
                param_deltas[k] = {"from": va, "to": vb}
        ta = a_ops[n].get("type")
        tb = b_ops[n].get("type")
        type_changed = ta != tb
        if param_deltas or type_changed:
            changed.append({
                "name": n,
                "type_changed": type_changed,
                "type": {"from": ta, "to": tb} if type_changed else None,
                "parameters": param_deltas,
            })

    def _conn_set(desc):
        s = set()
        for c in (desc.get("connections") or []):
            s.add((c.get("from"), c.get("to"), c.get("from_output", 0), c.get("to_input", 0)))
        for op in (desc.get("operators") or []):
            for i, src in enumerate(op.get("inputs", []) or []):
                if src:
                    s.add((src, op.get("name"), 0, i))
        return s

    ca, cb = _conn_set(a), _conn_set(b)
    conns_added = [{"from": f, "to": t, "from_output": fo, "to_input": ti}
                   for (f, t, fo, ti) in sorted(cb - ca)]
    conns_removed = [{"from": f, "to": t, "from_output": fo, "to_input": ti}
                     for (f, t, fo, ti) in sorted(ca - cb)]

    return {
        "ok": True,
        "operators": {"added": added, "removed": removed, "changed": changed,
                      "added_count": len(added), "removed_count": len(removed),
                      "changed_count": len(changed)},
        "connections": {"added": conns_added, "removed": conns_removed,
                        "added_count": len(conns_added), "removed_count": len(conns_removed)},
        "summary": (f"{len(added)} added, {len(removed)} removed, "
                    f"{len(changed)} changed operator(s); "
                    f"{len(conns_added)} added, {len(conns_removed)} removed connection(s)"),
    }
