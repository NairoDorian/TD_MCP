"""Tests for JSON-safe serialization: non-finite floats and numpy types.

Covers the fix inspired by touchdesigner-mcp-mrinalghosh commits:
  - de7cd23: Emit valid values for non-finite floats (inf/nan)
  - 074fa50: Serialize numpy scalars/arrays as numbers, not str()

Also verifies the MCP protocol version in the bridge was bumped to 2026-07-28.
"""

import json
import math
import os

from td_mcp.util import _json_safe, json_default, _NUMPY_AVAILABLE

if _NUMPY_AVAILABLE:
    import numpy as np


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE_PATH = os.path.join(HERE, "bridge", "td_mcp_bridge.py")


# ---------------------------------------------------------------------------
# Non-finite float handling
# ---------------------------------------------------------------------------

def test_non_finite_floats_become_none():
    assert _json_safe(float("inf")) is None
    assert _json_safe(float("-inf")) is None
    assert _json_safe(float("nan")) is None


def test_finite_floats_preserved():
    assert _json_safe(3.14) == 3.14
    assert _json_safe(0.0) == 0.0
    assert _json_safe(-1.5) == -1.5


def test_non_finite_in_nested_structures():
    nested = {"values": [1.0, float("inf"), float("-inf"), float("nan"), 2.0]}
    result = _json_safe(nested)
    assert result == {"values": [1.0, None, None, None, 2.0]}


def test_non_finite_via_json_dumps_is_valid():
    """json.dumps with _json_safe must not emit Infinity/NaN tokens."""
    data = {"a": float("inf"), "b": float("nan"), "c": float("-inf")}
    sanitized = json.dumps(_json_safe(data))
    # The output must be valid JSON — parse it back.
    parsed = json.loads(sanitized)
    assert parsed == {"a": None, "b": None, "c": None}


# ---------------------------------------------------------------------------
# Numpy handling (skipped if numpy not installed)
# ---------------------------------------------------------------------------

def test_numpy_scalar_converted():
    if not _NUMPY_AVAILABLE:
        import pytest
        pytest.skip("numpy not installed")
    assert _json_safe(np.float64(3.14)) == 3.14
    assert _json_safe(np.int32(42)) == 42
    assert _json_safe(np.bool_(True)) is True
    assert isinstance(_json_safe(np.float64(2.0)), float)


def test_numpy_array_converted_to_list():
    if not _NUMPY_AVAILABLE:
        import pytest
        pytest.skip("numpy not installed")
    arr = np.array([1.0, 2.0, 3.0])
    result = _json_safe(arr)
    assert result == [1.0, 2.0, 3.0]
    assert isinstance(result, list)


def test_numpy_non_finite_in_array():
    if not _NUMPY_AVAILABLE:
        import pytest
        pytest.skip("numpy not installed")
    arr = np.array([1.0, np.inf, np.nan, 4.0])
    result = _json_safe(arr)
    assert result == [1.0, None, None, 4.0]


def test_numpy_scalar_non_finite():
    if not _NUMPY_AVAILABLE:
        import pytest
        pytest.skip("numpy not installed")
    assert _json_safe(np.float64("inf")) is None
    assert _json_safe(np.float64("nan")) is None


def test_json_default_numpy():
    if not _NUMPY_AVAILABLE:
        import pytest
        pytest.skip("numpy not installed")
    # json_default should produce a serializable value for numpy types
    val = json_default(np.float64(3.14))
    assert val == 3.14


def test_json_default_non_finite_via_dumps():
    if not _NUMPY_AVAILABLE:
        import pytest
        pytest.skip("numpy not installed")
    # json.dumps with default=(_json_safe) should handle numpy without error
    data = {"arr": np.array([1.0, np.inf, 3.0])}
    result = json.dumps(_json_safe(data))
    parsed = json.loads(result)
    assert parsed == {"arr": [1.0, None, 3.0]}


# ---------------------------------------------------------------------------
# Basic type passthrough (no numpy needed)
# ---------------------------------------------------------------------------

def test_basic_types_passthrough():
    assert _json_safe(None) is None
    assert _json_safe(True) is True
    assert _json_safe(False) is False
    assert _json_safe(42) == 42
    assert _json_safe("hello") == "hello"


def test_nested_dict_and_list():
    data = {"inner": [1, "a", {"x": float("inf")}], "flag": True}
    result = _json_safe(data)
    assert result == {"inner": [1, "a", {"x": None}], "flag": True}


def test_bool_not_treated_as_int():
    # bool is a subclass of int in Python; ensure it stays bool.
    result = _json_safe(True)
    assert result is True


# ---------------------------------------------------------------------------
# Protocol version bump in bridge
# ---------------------------------------------------------------------------

def test_bridge_protocol_version_bumped():
    """The bridge's initialize response should advertise 2026-07-28, not 2024-11-05."""
    text = open(BRIDGE_PATH, encoding="utf-8").read()
    assert "2026-07-28" in text, "bridge should advertise MCP 2026-07-28 protocol"
    assert '"protocolVersion": "2024-11-05"' not in text, \
        "bridge should not still advertise the old 2024-11-05 protocol"


def test_bridge_json_safe_handles_non_finite():
    """The bridge's _json_safe source must reference math.isfinite for inf/nan."""
    text = open(BRIDGE_PATH, encoding="utf-8").read()
    assert "math.isfinite" in text, "bridge _json_safe must guard non-finite floats"


def test_bridge_json_safe_handles_numpy():
    """The bridge's _json_safe source must handle numpy generic/ndarray."""
    text = open(BRIDGE_PATH, encoding="utf-8").read()
    assert "_np.generic" in text or "np.generic" in text, \
        "bridge _json_safe must convert numpy scalars"
    assert "ndarray" in text, \
        "bridge _json_safe must convert numpy arrays"


if __name__ == "__main__":
    test_basic_types_passthrough()
    test_non_finite_floats_become_none()
    test_finite_floats_preserved()
    test_non_finite_in_nested_structures()
    test_non_finite_via_json_dumps_is_valid()
    test_nested_dict_and_list()
    test_bool_not_treated_as_int()
    test_bridge_protocol_version_bumped()
    test_bridge_json_safe_handles_non_finite()
    test_bridge_json_safe_handles_numpy()
    if _NUMPY_AVAILABLE:
        np.testing.assert_almost_equal(_json_safe(np.float64(3.14)), 3.14)
    print("\nALL JSON_SAFE TESTS PASSED")
