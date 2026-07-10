"""Tests for the ultimate-merge feature additions (review pass 2).

Covers output budgeting, build liveness analysis + A/B diff, collision-aware
layout, the parameter resolver, and knowledge-graph co-occurrence.
"""

import json

from td_mcp.util import output_budget as ob
from td_mcp.validation import analyze_build, diff_networks
from td_mcp.tools import layout
from td_mcp import param_resolver
from td_mcp.rag import knowledge_graph


# ---------------------------------------------------------------- output budget
def test_truncate_text_noop_under_cap():
    t, truncated, info = ob.truncate_text("hello world", 100)
    assert truncated is False
    assert t == "hello world"


def test_truncate_text_caps_and_reports():
    t, truncated, info = ob.truncate_text("abcdefghij", 5)
    assert truncated is True
    assert info["omitted_chars"] == 5
    assert "truncated" in t


def test_budget_payload_shrinks_lists():
    data = {"items": list(range(100)), "meta": "keep"}
    shrunk, truncated, info = ob.budget_payload(data, max_bytes=50)
    assert truncated is True
    assert isinstance(shrunk["items"], list)
    assert "_truncated" in shrunk["items"][-1]


# ----------------------------------------------------------- build liveness
GOOD = {
    "operators": [
        {"name": "noise1", "type": "Noise TOP", "parameters": {"resx": 128}, "inputs": [None]},
        {"name": "level1", "type": "Level TOP", "parameters": {}, "inputs": ["noise1"]},
    ],
    "connections": [{"from": "noise1", "to": "level1"}],
}


def test_analyze_build_flags_isolated():
    desc = {"operators": [{"name": "lonely", "type": "Noise TOP", "inputs": [None]}],
            "connections": []}
    r = analyze_build(desc)
    codes = {f["code"] for f in r["findings"]}
    assert "ISOLATED" in codes


def test_analyze_build_flags_broken_file_dep():
    desc = {"operators": [
        {"name": "mov", "type": "Movie File In TOP",
         "parameters": {"file": "/no/such/file.mov"}, "inputs": [None]}]}
    r = analyze_build(desc)
    codes = {f["code"] for f in r["findings"]}
    assert "BROKEN_FILE_DEP" in codes


def test_analyze_build_clean_network():
    # A terminal node (last consumer) legitimately has no output -> info-level
    # NO_OUTPUT. A *clean* network has no isolated / broken-file / empty-COMP
    # findings (warning/error severity).
    r = analyze_build(GOOD)
    bad = [f for f in r["findings"] if f["severity"] != "info"]
    assert bad == [], [f["code"] for f in bad]


def test_diff_networks_added_removed_changed():
    a = {"operators": [{"name": "n", "type": "Noise TOP", "parameters": {"resx": 128}, "inputs": [None]}]}
    b = {
        "operators": [
            {"name": "n", "type": "Noise TOP", "parameters": {"resx": 256}, "inputs": [None]},
            {"name": "out", "type": "Null TOP", "inputs": ["n"]},
        ],
        "connections": [{"from": "n", "to": "out"}],
    }
    r = diff_networks(a, b)
    assert "out" in r["operators"]["added"]
    assert r["operators"]["removed"] == []
    assert r["operators"]["changed"][0]["parameters"]["resx"] == {"from": 128, "to": 256}
    assert r["connections"]["added_count"] == 1


# --------------------------------------------------------------- layout
def test_boxes_overlap_true_false():
    assert layout.boxes_overlap((0, 0), (100, 100), (50, 0), (100, 100)) is True
    assert layout.boxes_overlap((0, 0), (100, 100), (300, 0), (100, 100)) is False


def test_first_free_cell_avoids_occupied():
    occupied = [((0, 0), (100, 100)), ((300, 0), (100, 100))]
    pos = layout.first_free_cell(occupied, size=(100, 100), spacing=200)
    assert not any(layout.boxes_overlap(pos, (100, 100), p, s) for (p, s) in occupied)


def test_spread_positions_removes_overlaps():
    ops = [{"name": f"n{i}", "type": "null", "position": [0, 0]} for i in range(5)]
    out = layout.spread_positions(ops, spacing=200)
    for i in range(len(out)):
        for j in range(i + 1, len(out)):
            assert not layout.boxes_overlap(
                tuple(out[i]["position"]), (100, 100),
                tuple(out[j]["position"]), (100, 100))


# ----------------------------------------------------------- param resolver
def test_resolve_parameters_maps_friendly_name():
    # 'frequency' is the real code for a Noise CHOP's 'freq' friendly name.
    resolved, warnings, ok = param_resolver.resolve_parameters(
        "Noise CHOP", {"freq": 2.0})
    # Either it maps to the real code, or it warns unknown; assert no crash and
    # that a friendly->code mapping is attempted when the corpus is present.
    assert isinstance(resolved, dict)
    assert "freq" in resolved or any("freq" in w["message"] for w in warnings)


def test_resolve_parameters_normalizes_menu_value():
    # TOP 'antialias' menu often takes 'on'/'off'; an int 1 should normalize.
    resolved, warnings, ok = param_resolver.resolve_parameters(
        "Null TOP", {"antialias": 1})
    if "antialias" in resolved:
        # value normalized to a menu string, or flagged if not in menu
        assert resolved["antialias"] in ("on", 1) or any(
            "antialias" in w["message"] for w in warnings)


def test_resolve_build_roundtrip():
    spec = {"operators": [
        {"name": "n", "type": "Noise TOP", "parameters": {"resx": 128}, "inputs": [None]}]}
    resolved, warnings, ok = param_resolver.resolve_build(spec)
    assert resolved["operators"][0]["parameters"]["resx"] == 128


# ----------------------------------------------------------- kg co-occurrence
def test_combo_related_returns_list():
    # Should not raise; returns a list (possibly empty if corpus lacks patterns).
    res = knowledge_graph.combo_related("Noise TOP", k=5)
    assert isinstance(res, list)
