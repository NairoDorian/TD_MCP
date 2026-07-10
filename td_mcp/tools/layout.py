"""Layout lint — Embody-style deterministic placement hygiene.

When an agent drops a batch of nodes, they tend to pile up at the origin or
overlap. Embody emits a ``LAYOUT WARNING`` whenever an operator lands at (0,0),
overlaps a sibling, or docks more than 500 units from its target. This module
implements that check as a pure function over a list of operator dicts, plus a
deterministic ``placement_hint`` so the live bridge can auto-spread new COMPs.

Run:  uv run python -m tests.test_layout
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Operators landing within this many units of the origin are suspicious.
ORIGIN_EPS = 1.0
# Two nodes with centers closer than this count as overlapping (piled up).
OVERLAP_DIST = 50.0
# A docked node further than this from its target is "stray".
DOCK_MAX_DIST = 500.0
DEFAULT_SPACING = 200


def _pos(op: Dict[str, Any]) -> Tuple[float, float]:
    p = op.get("position") or op.get("pos") or [0, 0]
    try:
        return float(p[0]), float(p[1])
    except (TypeError, IndexError, ValueError):
        return 0.0, 0.0


def _size(op: Dict[str, Any]) -> Tuple[float, float]:
    s = op.get("size") or [100, 100]
    try:
        return float(s[0]), float(s[1])
    except (TypeError, IndexError, ValueError):
        return 100.0, 100.0


def lint_layout(operators: List[Dict[str, Any]],
                spacing: float = DEFAULT_SPACING) -> Dict[str, Any]:
    """Return layout warnings for a list of operator dicts.

    Each operator may carry: name, position [x,y], size [w,h], dock (name of a
    docked target), and any other keys (ignored). The result is a stable,
    ordered list of warnings with codes so the agent can fix them.
    """
    warnings: List[Dict[str, Any]] = []
    seen: List[Tuple[str, Tuple[float, float], Tuple[float, float]]] = []

    for op in operators:
        name = op.get("name", "<unnamed>")
        x, y = _pos(op)
        w, h = _size(op)

        if not op.get("name"):
            warnings.append(_w("UNNAMED", name, "operator has no name; assign one",
                               {"position": [x, y]}))
        if abs(x) <= ORIGIN_EPS and abs(y) <= ORIGIN_EPS:
            warnings.append(_w("AT_ORIGIN", name,
                               "operator sits at (0,0) — likely an unplaced default",
                               {"position": [x, y]}))
        if w <= 0 or h <= 0:
            warnings.append(_w("ZERO_SIZE", name,
                               f"operator has non-positive size {[w, h]}",
                               {"size": [w, h]}))
        # Overlap with any previously seen node (center distance test).
        for prev_name, (px, py), _ in seen:
            if ((x - px) ** 2 + (y - py) ** 2) ** 0.5 < OVERLAP_DIST:
                warnings.append(_w("OVERLAP", name,
                                   f"operator overlaps {prev_name!r}",
                                   {"position": [x, y], "other": prev_name}))
                break
        # Stray dock.
        dock = op.get("dock")
        if dock:
            target = _find(operators, dock)
            if target is None:
                warnings.append(_w("DOCK_ORPHAN", name,
                                   f"docked to missing node {dock!r}", {}))
            else:
                tx, ty = _pos(target)
                if ((x - tx) ** 2 + (y - ty) ** 2) ** 0.5 > DOCK_MAX_DIST:
                    warnings.append(_w("DOCK_STRAY", name,
                                       f"docked to {dock!r} but {((x-tx)**2+(y-ty)**2)**0.5:.0f}u away",
                                       {"position": [x, y], "target": dock}))
        seen.append((name, (x, y), (w, h)))

    return {
        "ok": len(warnings) == 0,
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def placement_hint(operators: List[Dict[str, Any]],
                   spacing: float = DEFAULT_SPACING,
                   anchor: Tuple[float, float] = (0, 0)) -> Tuple[float, float]:
    """Deterministic next-free position: place new nodes left-to-right in a row
    starting at ``anchor``, stepping by ``spacing``. Avoids the (0,0) pile-up."""
    max_x = anchor[0]
    for op in operators:
        x, _ = _pos(op)
        max_x = max(max_x, x)
    return (max_x + spacing, anchor[1])


def _find(operators: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    for op in operators:
        if op.get("name") == name:
            return op
    return None


def _w(code: str, name: Any, message: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    return {"code": code, "name": name, "message": message, "detail": detail}
