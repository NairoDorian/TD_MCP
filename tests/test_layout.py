"""Tests for td_mcp.tools.layout (Embody-style layout hygiene)."""

from td_mcp.tools.layout import lint_layout, placement_hint


def test_at_origin_warning():
    ops = [{"name": "a", "position": [0, 0], "size": [100, 100]}]
    r = lint_layout(ops)
    codes = {w["code"] for w in r["warnings"]}
    assert "AT_ORIGIN" in codes


def test_overlap_and_stray_dock():
    ops = [
        {"name": "a", "position": [0, 0], "size": [100, 100]},
        {"name": "b", "position": [0, 1], "size": [100, 100], "dock": "c"},
    ]
    r = lint_layout(ops)
    codes = {w["code"] for w in r["warnings"]}
    assert "AT_ORIGIN" in codes
    assert "OVERLAP" in codes
    assert "DOCK_ORPHAN" in codes


def test_clean_layout_ok():
    ops = [
        {"name": "a", "position": [0, 0], "size": [100, 100]},
        {"name": "b", "position": [300, 0], "size": [100, 100]},
    ]
    r = lint_layout(ops)
    # (0,0) is still flagged but overlap is not
    codes = {w["code"] for w in r["warnings"]}
    assert "OVERLAP" not in codes


def test_zero_size():
    ops = [{"name": "a", "position": [500, 500], "size": [0, 0]}]
    r = lint_layout(ops)
    assert any(w["code"] == "ZERO_SIZE" for w in r["warnings"])


def test_placement_hint_avoids_pileup():
    ops = [{"name": "a", "position": [400, 0], "size": [100, 100]}]
    pos = placement_hint(ops, spacing=200)
    assert pos == (600, 0)


def test_unnamed_flagged():
    ops = [{"position": [500, 500], "size": [100, 100]}]
    r = lint_layout(ops)
    assert any(w["code"] == "UNNAMED" for w in r["warnings"])
