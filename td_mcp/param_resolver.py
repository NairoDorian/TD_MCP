"""Parameter name + menu-value resolver (TD_Builder_alpha param_name_resolver idea).

Agents/LLMs tend to emit *friendly* parameter names and ints/booleans
(``freq`` -> ``frequency``, ``brightness`` -> ``brightness1``, ``0`` -> ``"add"``)
that silently no-op when written to TouchDesigner because ``set_parameters`` /
``create_node`` expect the exact parameter *code*. This module normalizes an
incoming params dict against the operator schema probed from a running
TouchDesigner (``td_mcp.kb.param_catalog``):

  * maps a friendly key / UI label / numbered base to the exact parameter name
  * expands tuplets (``resolution: [512, 512]`` -> ``resolutionw``/``resolutionh``)
  * normalizes a menu value (token, label or index) to a valid menu token
  * flags unknown params / out-of-menu values with did-you-mean warnings

Pure stdlib; passes params through untouched when the catalog is absent.
"""

from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional, Tuple

from td_mcp.kb import param_catalog as _catalog


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


#: Normalized foreign spelling -> TD name. Checked against the schema before
#: use, so an alias can never invent a parameter an operator lacks.
_ALIASES = {
    "resx": "resolutionw", "resy": "resolutionh", "res": "resolution",
    "width": "resolutionw", "height": "resolutionh",
    "translate": "t", "rotate": "r", "scale": "s", "pivot": "p",
    "opacity": "opacity", "color": "color",
}


def _pick_op_type(op_type: Optional[str], params: Dict[str, Any]
                  ) -> Tuple[Optional[str], Optional[str]]:
    """``(opType, ambiguity_note)`` for a caller-supplied type spelling.

    A bare short type ('noise', 'constant') exists in several families. Rather
    than guess the first, pick the candidate whose schema already contains the
    most of the caller's keys -- the keys are evidence of what was meant -- and
    say so.
    """
    if not (op_type and _catalog.available()):
        return None, None
    hit = _catalog.resolve_op_type(op_type)
    if hit:
        return hit, None
    options = _catalog.candidates(op_type)
    if len(options) < 2:
        return None, None
    keys = set(params or {})
    scored = sorted(options, key=lambda t: -len(keys & set(_catalog.schema(t))))
    best = scored[0]
    return best, (f"{op_type!r} is ambiguous ({', '.join(options)}); assumed "
                  f"{best} from the parameter names -- pass the opType to be sure")


def _map_key(op_type: str, key: str, schema: Dict[str, Any],
             tuplets: Dict[str, List[str]]) -> Tuple[Optional[str], str]:
    """``(name_or_tuplet, how)`` for one caller key; how is '' for exact."""
    if key in schema or key in tuplets:
        return key, ""
    sq = _norm(key)
    # Case / separator differences: 'Resolution_W' -> 'resolutionw'.
    for name in schema:
        if _norm(name) == sq:
            return name, "case"
    for tn in tuplets:
        if _norm(tn) == sq:
            return tn, "case"
    # The label an agent read off the UI: 'Clamp Input' -> 'clampinput'. A
    # tuplet's components share one label, so a label can name the tuplet.
    by_label: Dict[str, List[str]] = {}
    for name, info in schema.items():
        by_label.setdefault(_norm(info.get("label") or ""), []).append(name)
    if sq in by_label:
        names = by_label[sq]
        tn = schema[names[0]].get("tuplet")
        if len(names) > 1 and tn:
            return tn, "label"
        return names[0], "label"
    # Spellings other tools teach agents, honoured only when the target exists.
    alias = _ALIASES.get(sq)
    if alias and (alias in schema or alias in tuplets):
        return alias, "alias"
    # A numbered family's base name: 'brightness' -> 'brightness1'.
    numbered = sorted(n for n in schema if n.startswith(key.lower())
                      and n[len(key):].isdigit())
    if numbered:
        return numbered[0], "prefix"
    # An abbreviation that names exactly one parameter: 'freq' -> 'frequency'.
    if len(sq) >= 3:
        unique = [n for n in schema if n.startswith(sq)]
        if len(unique) == 1:
            return unique[0], "prefix"
    close = difflib.get_close_matches(key.lower(), list(schema), n=1, cutoff=0.8)
    if close:
        return close[0], "fuzzy"
    return None, ""


def _normalize_value(name: str, info: Dict[str, Any], val: Any,
                     warnings: List[Dict[str, str]]) -> Tuple[Any, bool]:
    """``(value, valid)`` after menu / toggle / range checks for one param."""
    if isinstance(val, dict):
        # {"expr": ...} / {"pulse": true}: an instruction, not a value to check
        return val, True
    menu = info.get("menu") or []
    if menu:
        if val in menu:
            return val, True
        labels = info.get("menu_labels") or []
        sval = str(val).strip().lower()
        for i, lab in enumerate(labels):
            if str(lab).strip().lower() == sval and i < len(menu):
                warnings.append({"param": name, "message":
                                 f"menu label {val!r} -> token {menu[i]!r}"})
                return menu[i], True
        mv = _match_menu(val, menu, cutoff=0.75)
        if mv is not None:
            warnings.append({"param": name, "message":
                             f"normalized menu value {val!r} -> {mv!r}"})
            return mv, True
        warnings.append({"param": name, "message":
                         f"value {val!r} not in menu {menu[:10]}"
                         f"{'...' if len(menu) > 10 else ''}"})
        return val, False
    style = info.get("style")
    if style == "Toggle" and isinstance(val, str):
        low = val.strip().lower()
        if low in ("on", "true", "yes", "1"):
            return True, True
        if low in ("off", "false", "no", "0"):
            return False, True
    if style == "Pulse":
        warnings.append({"param": name, "message":
                         "pulse parameter: setting a value does nothing -- use "
                         "pulse_parameter / par.<name>.pulse()"})
    if info.get("read_only"):
        warnings.append({"param": name, "message": "read-only parameter: TD will refuse the write"})
    lo, hi = info.get("min"), info.get("max")
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if lo is not None and val < lo:
            warnings.append({"param": name, "message": f"{val} is below the hard minimum {lo}; TD clamps it"})
        if hi is not None and val > hi:
            warnings.append({"param": name, "message": f"{val} is above the hard maximum {hi}; TD clamps it"})
    return val, True


def resolve_parameters(op_type: Optional[str], params: Dict[str, Any]
                       ) -> Tuple[Dict[str, Any], List[Dict[str, str]], bool]:
    """Normalize ``params`` for one operator against the live-probed catalog.

    Returns ``(resolved_params, warnings, ok)`` where ``warnings`` is a list of
    ``{"param", "message"}`` dicts so the agent can see what was changed.

    * keys: exact name, case/separator, UI label, numbered base
      (``brightness`` -> ``brightness1``), or a close fuzzy match (cutoff 0.8)
    * tuplets: ``resolution: [512, 512]`` -> ``resolutionw/h``; a scalar on a
      tuplet is broadcast to every component, with a warning
    * values: menu token, menu label, index or close token; toggle strings;
      hard-clamp, read-only and pulse warnings
    * ``ok`` is False when a key matches nothing or a menu value is invalid.

    Without a catalog entry for the type the params pass through untouched --
    the old fallback rewrote correct names into the scraped corpus's *labels*
    (``brightness1`` -> ``'Brightness 1'``), which TouchDesigner rejects.
    """
    warnings: List[Dict[str, str]] = []
    resolved: Dict[str, Any] = {}
    ok = True
    ot, note = _pick_op_type(op_type, params)
    if note:
        warnings.append({"param": "*", "message": note})
    if not ot:
        if op_type and _catalog.available():
            warnings.append({"param": "*", "message":
                             f"unknown operator type {op_type!r}; parameters not checked"})
        return dict(params or {}), warnings, ok
    schema = _catalog.schema(ot)
    tuplets = _catalog.tuplets(ot)

    for raw_key, val in (params or {}).items():
        mapped, how = _map_key(ot, raw_key, schema, tuplets)
        if mapped is None:
            ok = False
            hint = _catalog.suggest(ot, raw_key)
            warnings.append({"param": raw_key, "message":
                             f"unknown parameter {raw_key!r} for {ot}"
                             + (f" (did you mean {', '.join(hint)}?)" if hint else "")})
            resolved[raw_key] = val
            continue
        if how:
            warnings.append({"param": raw_key, "message":
                             f"resolved parameter name {raw_key!r} -> {mapped!r} ({how})"})
        if mapped in tuplets and mapped not in schema:
            comps = tuplets[mapped]
            if isinstance(val, (list, tuple)):
                if len(val) > len(comps):
                    warnings.append({"param": raw_key, "message":
                                     f"{mapped} has {len(comps)} components; extra values dropped"})
                pairs = list(zip(comps, val, strict=False))  # extras dropped, warned above
            else:
                warnings.append({"param": raw_key, "message":
                                 f"scalar on tuplet {mapped!r} broadcast to {', '.join(comps)}"})
                pairs = [(c, val) for c in comps]
            for comp, v in pairs:
                v, valid = _normalize_value(comp, schema[comp], v, warnings)
                ok = ok and valid
                resolved[comp] = v
            continue
        v, valid = _normalize_value(mapped, schema[mapped], val, warnings)
        ok = ok and valid
        resolved[mapped] = v
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
