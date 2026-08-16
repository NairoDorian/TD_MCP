"""Parameter name + menu-value resolver (TD_Builder_alpha param_name_resolver idea).

Agents/LLMs tend to emit *friendly* parameter names and ints/booleans
(``freq`` -> ``frequency``, ``brightness`` -> ``brightness1``, ``0`` -> ``"add"``)
that silently no-op when written to TouchDesigner because ``set_parameters`` /
``create_node`` expect the exact parameter *code*. This module normalizes an
incoming params dict against the real operator schema from the corpus:

  * fuzzy-maps a friendly key to the exact TD parameter code
  * normalizes a menu value to a valid ``menuItems`` entry
  * flags unknown params / out-of-menu values with actionable warnings

Pure stdlib + the existing corpus; degrades gracefully when the corpus is absent.
"""

from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional, Tuple

try:
    from td_mcp.kb import corpus as _corpus

    _CORPUS_OK = True
except Exception:  # noqa: BLE001
    _corpus = None
    _CORPUS_OK = False


def _norm(s: str) -> str:
    return s.strip().lower().replace(" ", "").replace("_", "").replace("-", "")


def _match_key(key: str, candidates: List[str], cutoff: float = 0.6) -> Optional[str]:
    """Return the best matching candidate param name for a (possibly friendly) key."""
    if not candidates:
        return None
    kn = _norm(key)
    if not kn:
        return None
    # Exact (normalized) match first.
    for c in candidates:
        if _norm(c) == kn:
            return c
    # Prefix / containment (e.g. 'brightness' -> 'brightness1').
    for c in candidates:
        cn = _norm(c)
        if cn.startswith(kn) or kn.startswith(cn):
            return c
    # Fuzzy fallback.
    hit = difflib.get_close_matches(key, candidates, n=1, cutoff=cutoff)
    return hit[0] if hit else None


def _match_menu(value: Any, menu: List[Any], cutoff: float = 0.6) -> Optional[Any]:
    """Return the best matching menu entry for an (often loosely typed) value."""
    if not menu:
        return None
    sv = str(value).strip().lower()
    for m in menu:
        if str(m).strip().lower() == sv:
            return m
    # Numeric menu indices ("0" -> first item) when values are int-like.
    try:
        idx = int(value)
        if 0 <= idx < len(menu):
            return menu[idx]
    except (TypeError, ValueError):
        pass
    hit = difflib.get_close_matches(str(value), [str(m) for m in menu], n=1, cutoff=cutoff)
    if hit:
        for m in menu:
            if str(m) == hit[0]:
                return m
    return None


def resolve_parameters(op_type: Optional[str], params: Dict[str, Any]
                       ) -> Tuple[Dict[str, Any], List[Dict[str, str]], bool]:
    """Normalize ``params`` for one operator.

    Returns ``(resolved_params, warnings, ok)`` where ``warnings`` is a list of
    ``{"param", "message"}`` dicts so the agent can see what was changed.
    """
    warnings: List[Dict[str, str]] = []
    resolved: Dict[str, Any] = {}

    schema: Dict[str, Any] = {}
    if _CORPUS_OK and op_type:
        rec = _corpus.operator_record(op_type)
        if rec:
            schema = _corpus.param_schema(rec) or {}

    candidate_keys = list(schema.keys())

    for raw_key, val in (params or {}).items():
        key = raw_key
        if candidate_keys and raw_key not in schema:
            mapped = _match_key(raw_key, candidate_keys)
            if mapped and mapped != raw_key:
                warnings.append({"param": raw_key,
                                 "message": f"resolved parameter name {raw_key!r} -> {mapped!r}"})
                key = mapped
            elif not mapped:
                warnings.append({"param": raw_key,
                                 "message": f"unknown parameter {raw_key!r} for {op_type or 'operator'}"})

        if key in schema:
            info = schema[key]
            menu = info.get("menu") or []
            if menu and val not in menu:
                mv = _match_menu(val, menu)
                if mv is not None and mv != val:
                    warnings.append({"param": key,
                                     "message": f"normalized menu value {val!r} -> {mv!r}"})
                    val = mv
                elif mv is None:
                    warnings.append({"param": key,
                                     "message": f"value {val!r} not in menu {menu[:8]}{'...' if len(menu) > 8 else ''}"})

        resolved[key] = val

    ok = not any("unknown parameter" in w["message"] for w in warnings)
    return resolved, warnings, ok


# ---------------------------------------------------------------------------
# Parameter-family collapse/expansion (TrueFiasco technique, MIT-safe reimpl).
# ---------------------------------------------------------------------------
_XYZ_FAMILIES = {"t", "r", "s", "p", "scale", "pos", "rot", "color", "rgb"}
_INDEXED_FAMILIES = {"fromrange", "torange", "const", "fills", "value"}


def _strip_trailing_digits(name):
    return name.rstrip("0123456789")


def expand_family_parameter(name, value):
    """Expand a list-valued param into TouchDesigner's flat suffix params.

    Returns a dict ``{param_name: value}``. Rules (described, not copied from
    AGPL): XYZ-like bases get letter suffixes (x/y/z), indexed bases get
    numeric suffixes (1/2/3), 3-element lists default to x/y/z (except
    color/rgb -> r/g/b), 2-element lists default to 1/2. Non-list values are
    returned unchanged under the original name.
    """
    if not isinstance(value, (list, tuple)):
        return {name: value}
    if len(value) == 1:
        return {name: value[0]}
    base = _strip_trailing_digits(name)
    suffixes = None
    if base in _XYZ_FAMILIES:
        if base in ("color", "rgb"):
            suffixes = ["r", "g", "b", "a"][:len(value)]
        else:
            suffixes = ["x", "y", "z", "w"][:len(value)]
    elif base in _INDEXED_FAMILIES:
        suffixes = [str(i + 1) for i in range(len(value))]
    elif len(value) == 3:
        suffixes = ["x", "y", "z"]
    elif len(value) == 2:
        suffixes = ["1", "2"]
    if suffixes is None:
        return {name: value}
    return {f"{base}{suffixes[i]}": value[i] for i in range(len(value))}


def expand_params(params):
    """Expand every list-valued param in ``params`` via :func:`expand_family_parameter`."""
    out = {}
    for k, v in (params or {}).items():
        out.update(expand_family_parameter(k, v))
    return out


def resolve_build(spec: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], bool]:
    """Resolve parameters for every operator in a build description.

    Returns ``(resolved_spec, per_op_warnings, ok)``.
    """
    warnings: List[Dict[str, Any]] = []
    ok = True
    out_ops = []
    for op in (spec.get("operators") or []):
        params = op.get("parameters") or {}
        resolved, w, op_ok = resolve_parameters(op.get("type"), params)
        if not op_ok:
            ok = False
        if w:
            warnings.append({"operator": op.get("name"), "issues": w})
        new_op = dict(op)
        new_op["parameters"] = resolved
        out_ops.append(new_op)
    resolved_spec = {"operators": out_ops,
                     "connections": [dict(c) for c in (spec.get("connections") or [])]}
    return resolved_spec, warnings, ok
