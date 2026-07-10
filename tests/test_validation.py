"""Tests for td_mcp.validation (TD_Builder 5-stage + tdmcp auto-repair)."""

from td_mcp.validation import (
    auto_repair,
    suggest_repairs,
    validate_build,
)


GOOD = {
    "operators": [
        {"name": "noise1", "type": "Noise TOP", "parameters": {"resx": 128},
         "inputs": [None]},
        {"name": "level1", "type": "Level TOP", "parameters": {},
         "inputs": ["noise1"]},
    ],
    "connections": [{"from": "noise1", "to": "level1", "from_output": 0, "to_input": 0}],
}


def test_valid_network_passes():
    r = validate_build(GOOD)
    assert r["ok"] is True, r["summary"]
    assert r["error_count"] == 0
    assert len(r["stages"]) == 5


def test_dangling_source_detected():
    desc = {"operators": [{"name": "a", "type": "Noise TOP", "inputs": ["ghost"]}]}
    r = validate_build(desc)
    codes = {f["code"] for f in r["findings"]}
    assert "DANGLING_SRC" in codes
    assert r["ok"] is False


def test_family_mismatch_detected():
    desc = {
        "operators": [
            {"name": "n", "type": "Noise TOP", "inputs": [None]},
            {"name": "c", "type": "Noise CHOP", "inputs": ["n"]},
        ],
        "connections": [{"from": "n", "to": "c"}],
    }
    r = validate_build(desc)
    codes = {f["code"] for f in r["findings"]}
    assert "FAMILY_MISMATCH" in codes


def test_unknown_type_warning_not_error():
    desc = {"operators": [{"name": "x", "type": "Mystery OP", "inputs": [None]}]}
    r = validate_build(desc, strict=False)
    codes = {f["code"] for f in r["findings"]}
    assert "UNKNOWN_TYPE" in codes
    assert all(f["severity"] != "error" for f in r["findings"])


def test_auto_repair_drops_dangling():
    desc = {
        "operators": [
            {"name": "a", "type": "Noise TOP", "inputs": ["ghost"]},
            {"name": "b", "type": "Level TOP", "inputs": ["a"]},
        ],
        "connections": [{"from": "ghost", "to": "b"}],
    }
    report = validate_build(desc)
    fixed = auto_repair(desc, report)
    r2 = validate_build(fixed)
    assert r2["ok"] is True, r2["summary"]


def test_auto_repair_autonames_and_drops_typeless():
    desc = {
        "operators": [
            {"name": "", "type": "Noise TOP", "inputs": [None]},
            {"name": "named", "type": "", "inputs": [None]},
        ],
    }
    fixed = auto_repair(desc)
    names = [o["name"] for o in fixed["operators"]]
    assert any(names)
    assert all(o.get("type") for o in fixed["operators"])
    # typeless node dropped
    assert len(fixed["operators"]) == 1


def test_suggest_repairs_nonempty():
    desc = {"operators": [{"name": "a", "type": "Noise TOP", "inputs": ["ghost"]}]}
    report = validate_build(desc)
    repairs = suggest_repairs(report)
    assert any(rp["action"] == "drop_input" for rp in repairs)
