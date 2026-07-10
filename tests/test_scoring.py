"""Tests for td_mcp.scoring (scoreBuild + repair_network loop)."""

from td_mcp.scoring import repair_network, score_build
from td_mcp.validation import validate_build

GOOD = {
    "operators": [
        {"name": "n", "type": "Noise TOP", "inputs": [None]},
        {"name": "l", "type": "Level TOP", "inputs": ["n"]},
    ],
    "connections": [{"from": "n", "to": "l"}],
}


def test_good_scores_high():
    sc = score_build(GOOD)
    assert sc["score"] >= 90
    assert sc["grade"] in ("A", "B")
    assert sc["ok"] is True


def test_bad_scores_low():
    bad = {"operators": [{"name": "a", "type": "Noise TOP", "inputs": ["ghost"]}]}
    sc = score_build(bad)
    assert sc["score"] < 100
    assert sc["error_count"] >= 1


def test_repair_network_fixes():
    broken = {
        "operators": [
            {"name": "a", "type": "Noise TOP", "inputs": ["ghost"]},
            {"name": "b", "type": "Level TOP", "inputs": ["a"]},
        ],
        "connections": [{"from": "ghost", "to": "b"}],
    }
    fixed, iters, sc = repair_network(broken)
    assert validate_build(fixed)["ok"] is True
    assert iters >= 1
    assert sc["ok"] is True


def test_repair_network_idempotent_clean():
    fixed, iters, sc = repair_network(GOOD)
    assert iters == 0
    assert sc["ok"] is True
