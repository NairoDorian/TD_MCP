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


# ---------------------------------------------------------------------------
# Wave 12: live diagnostics reach the scorer
#
# Before this wave the loop had two broken edges. `build_and_verify` captured
# `get_errors` and never passed it to the scorer, and it passed the whole
# `capture_viewport` envelope where the inner `verdict` was expected — so
# `is_black` read as absent and no viewport penalty could fire either. On top
# of that the bridge could not read TD's errors at all, so the payload would
# have been empty regardless. A network that was red in TouchDesigner scored A.
# ---------------------------------------------------------------------------

CLEAN_SPEC = {"operators": [
    {"name": "n1", "type": "Noise TOP", "parameters": {}, "inputs": [None]},
]}


def _diag(kind, n=1):
    return {"diagnostics": [{"message": f"{kind} problem {i}",
                             "severity": "warning" if kind == "warning" else "error",
                             "kind": kind} for i in range(n)]}


def test_cook_error_lowers_the_score():
    base = scoring.score_build(CLEAN_SPEC)
    hurt = scoring.score_build({**CLEAN_SPEC, "live_diagnostics": _diag("cook")})
    assert hurt["score"] < base["score"]
    assert hurt["live_error_count"] == 1
    assert hurt["ok"] is False


def test_script_traceback_lowers_the_score():
    """A DAT whose onCook raises is a structurally perfect description."""
    base = scoring.score_build(CLEAN_SPEC)
    assert base["ok"] is True
    hurt = scoring.score_build({**CLEAN_SPEC, "live_diagnostics": _diag("script")})
    assert hurt["score"] < base["score"]
    assert "1 script error" in hurt["live_flags"]
    assert hurt["ok"] is False


def test_glsl_compile_failure_lowers_the_score():
    hurt = scoring.score_build({**CLEAN_SPEC, "live_diagnostics": _diag("shader")})
    assert "1 shader error" in hurt["live_flags"]
    assert hurt["ok"] is False


def test_live_warnings_are_not_failures():
    hurt = scoring.score_build({**CLEAN_SPEC,
                                "live_diagnostics": _diag("warning", 2)})
    assert hurt["ok"] is True
    assert hurt["live_error_count"] == 0
    assert "2 warnings" in hurt["live_flags"]


def test_clean_live_diagnostics_change_nothing():
    base = scoring.score_build(CLEAN_SPEC)
    same = scoring.score_build({**CLEAN_SPEC, "live_diagnostics": {
        "diagnostics": [], "errorCount": 0}})
    assert same["score"] == base["score"]
    assert same["live_flags"] == []


def test_all_three_error_kinds_accumulate():
    hurt = scoring.score_build({**CLEAN_SPEC, "live_diagnostics": [
        _diag("cook"), _diag("script"), _diag("shader")]})
    assert hurt["live_error_count"] == 3
    assert hurt["score"] < scoring.score_build(CLEAN_SPEC)["score"] - 40


def test_td_validate_live_accepts_diagnostics_as_json_string():
    out = json.loads(td_validate_live(json.dumps(CLEAN_SPEC),
                                      live_diagnostics=json.dumps(_diag("script"))))
    assert out["score"]["live_error_count"] == 1


def test_td_validate_live_accepts_a_raw_get_errors_payload():
    """The exact shape the bridge's get_errors returns, passed through whole."""
    payload = {
        "ok": True, "path": "/project1/glsl1",
        "errors": ["0(12) : error C1008"], "warnings": [],
        "diagnostics": [{"message": "0(12) : error C1008",
                         "severity": "error", "kind": "shader"}],
        "errorCount": 1, "hasErrors": True,
    }
    out = json.loads(td_validate_live(CLEAN_SPEC, live_diagnostics=payload))
    assert out["score"]["live_error_count"] == 1
    assert out["score"]["ok"] is False


def test_build_and_verify_feeds_both_live_signals_to_the_scorer():
    """End-to-end: create → errors → viewport → offline validate/score.

    Drives the real `TDClient.build_and_verify` against a stubbed bridge so the
    wiring itself is under test, which is where both edges were broken.
    """
    from td_mcp.server_live import TDClient

    client = TDClient.__new__(TDClient)
    calls = []

    def fake_call(tool, args=None):
        calls.append(tool)
        if tool == "create_node":
            return {"ok": True, "path": "/project1/glsl1"}
        if tool == "get_errors":
            return {"ok": True, "path": "/project1/glsl1",
                    "errors": ["0(12) : error C1008: undefined variable"],
                    "warnings": [], "errorCount": 1, "hasErrors": True,
                    "diagnostics": [{"message": "0(12) : error C1008",
                                     "severity": "error", "kind": "shader"}]}
        if tool == "capture_viewport":
            return {"ok": True, "file": "/tmp/x.png",
                    "verdict": {"is_black": True, "is_flat": True,
                                "caption": "Viewport is black"}}
        return {"ok": True}

    client._call = fake_call
    out = client.build_and_verify("/project1", "glslTOP")

    assert "offline_validation" in out, calls
    assessed = json.loads(out["offline_validation"])
    score = assessed["score"]
    assert score["live_error_count"] == 1, score
    assert "1 shader error" in score["live_flags"]
    assert "black" in score["viewport_flags"], score
    assert score["ok"] is False
