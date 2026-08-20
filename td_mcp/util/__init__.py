"""Internal utilities for td-mcp (output budgeting, safe IO, etc.)."""

import math

# numpy is an optional dependency (only present with the ``rag`` extra).
try:
    import numpy as _np
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False
    _np = None


def json_default(obj):
    """JSON ``default`` handler for non-native types.

    Lets ``json.dumps`` serialize numpy scalars/arrays and TD objects instead
    of raising ``TypeError``.  Non-finite floats (``inf``/``nan``) are *not*
    caught here because ``json.dumps`` handles ``float`` natively (and would
    emit the non-standard ``Infinity``/``NaN`` tokens); use :func:`_json_safe`
    to recurse-pre-process values that may contain them.
    """
    if _NUMPY_AVAILABLE:
        if isinstance(obj, _np.generic):
            return obj.item()
        if isinstance(obj, _np.ndarray):
            return obj.tolist()
    # TD objects with path/name → dict.
    if hasattr(obj, "path") and hasattr(obj, "name"):
        return {"path": str(obj.path), "name": str(obj.name),
                "type": str(getattr(obj, "type", type(obj).__name__))}
    return str(obj)


def _json_safe(value):
    """Recursively sanitize a value for JSON serialization.

    - numpy scalars (np.float64, np.int32, np.bool_, …) → native Python types
    - numpy arrays → nested lists
    - non-finite floats (inf / -inf / nan) → None (valid JSON; the raw tokens
      ``Infinity`` / ``NaN`` are rejected by strict parsers)

    This is the client-side counterpart of the bridge's ``_json_safe`` so both
    halves of the split handle numpy data the same way (mrinalghosh).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return value
    if _NUMPY_AVAILABLE:
        if isinstance(value, _np.generic):
            return _json_safe(value.item())
        if isinstance(value, _np.ndarray):
            return [_json_safe(v) for v in value.tolist()]
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in list(value)]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if hasattr(value, "path") and hasattr(value, "name"):
        return {"path": str(value.path), "name": str(value.name),
                "type": str(getattr(value, "type", type(value).__name__))}
    return str(value)
