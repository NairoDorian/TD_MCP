"""Tests for td_mcp.heal (self-healing orchestrator)."""

from td_mcp.heal import assess_build, self_heal

BROKEN = {
    "operators": [
        {"name": "a", "type": "Noise TOP", "inputs": ["ghost"]},
        {"name": "b", "type": "Level TOP", "inputs": ["a"]},
    ],
    "connections": [{"from": "ghost", "to": "b"}],
}

GOOD = {
    "operators": [
        {"name": "n", "type": "Noise TOP", "inputs": [None]},
        {"name": "l", "type": "Level TOP", "inputs": ["n"]},
    ],
    "connections": [{"from": "n", "to": "l"}],
}


def test_assess_flags_errors():
    a = assess_build(BROKEN)
    assert a["ok"] is False
    assert a["validation"]["error_count"] >= 1
    assert a["repairs"]
    assert a["recovery"]  # recovery hints attached for errors


def test_self_heal_cleans():
    h = self_heal(BROKEN)
    assert h["iterations"] >= 1
    assert h["ok"] is True
    assert h["improved"] is True


def test_self_heal_clean_passthrough():
    h = self_heal(GOOD)
    assert h["iterations"] == 0
    assert h["ok"] is True


def test_score_present_in_assessment():
    a = assess_build(GOOD)
    assert "score" in a and "grade" in a["score"]
