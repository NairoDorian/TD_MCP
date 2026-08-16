"""Tests for the live↔offline validation loop (§3.1)."""

import json

from td_mcp import scoring
from td_mcp.server_offline import td_validate_live, td_score_build
from td_mcp.vision import viewport_verdict, make_test_image


def test_score_build_penalizes_black_viewport():
    good = {"operators": [
        {"name": "n1", "type": "Noise TOP", "parameters": {}, "inputs": [None]},
        {"name": "n2", "type": "Level TOP", "parameters": {}, "inputs": ["n1"]},
    ], "connections": [{"from": "n1", "to": "n2"}]}
    base = scoring.score_build(good)
    assert base["score"] >= 90, base

    # Same network but with a black viewport verdict should score lower.
    pix, w, h = make_test_image("black")
    vv = viewport_verdict(pix, w, h)
    penalized = scoring.score_build({**good, "viewport_verdict": vv})
    assert penalized["score"] < base["score"]
    assert "black" in penalized["viewport_flags"]


def test_score_build_penalizes_flat_viewport():
    good = {"operators": [
        {"name": "n1", "type": "Noise TOP", "parameters": {}, "inputs": [None]},
    ], "connections": []}
    pix, w, h = make_test_image("flat")
    vv = viewport_verdict(pix, w, h)
    penalized = scoring.score_build({**good, "viewport_verdict": vv})
    assert "flat" in penalized["viewport_flags"]
    assert penalized["score"] < scoring.score_build(good)["score"]


def test_td_validate_live_with_viewport_verdict():
    spec = {"operators": [
        {"name": "n1", "type": "Noise TOP", "parameters": {}, "inputs": [None]},
    ], "connections": []}
    pix, w, h = make_test_image("colorful")
    vv = viewport_verdict(pix, w, h)
    out = json.loads(td_validate_live(spec, viewport_verdict=vv))
    assert "validation" in out and "score" in out
    # Colorful viewport is "pass" → no viewport penalty flags.
    assert out["score"]["viewport_flags"] == []


def test_td_validate_live_with_black_viewport_string():
    spec = '{"operators": [{"name": "n1", "type": "Noise TOP", "inputs": [null]}]}'
    pix, w, h = make_test_image("black")
    vv = viewport_verdict(pix, w, h)
    out = json.loads(td_validate_live(spec, viewport_verdict=json.dumps(vv)))
    assert "black" in out["score"]["viewport_flags"]
    assert out["score"]["score"] <= 80


def test_td_score_build_with_viewport_verdict_string():
    spec = '{"operators": [{"name": "n1", "type": "Noise TOP", "inputs": [null]}], "viewport_verdict": {"is_black": true, "is_flat": false, "fully_transparent": false, "caption": "Viewport is black"}}'
    out = json.loads(td_score_build(spec))
    assert "black" in out["viewport_flags"]


def test_td_validate_live_without_viewport():
    spec = '{"operators": [{"name": "n1", "type": "Noise TOP", "inputs": [null]}]}'
    out = json.loads(td_validate_live(spec))
    assert "validation" in out and "score" in out
    assert out["score"]["viewport_flags"] == []
