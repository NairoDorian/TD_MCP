"""Tests for td_mcp.perf (performance analyzer)."""

from td_mcp.perf import analyze_performance


def test_low_fps_flagged():
    r = analyze_performance({"fps": 12, "nodes": []})
    assert r["fps_ok"] is False
    assert any("low" in s.lower() for s in r["suggestions"])


def test_slow_node_suggested():
    r = analyze_performance({"fps": 60, "nodes": [
        {"name": "/p/heavy", "cook_time": 30.0, "cpu": 90, "gpu": 10},
        {"name": "/p/light", "cook_time": 0.2, "cpu": 5, "gpu": 1},
    ]})
    assert r["slowest"][0]["name"] == "/p/heavy"
    assert any("heavy" in s.lower() or "cpu" in s.lower() for s in r["suggestions"])


def test_healthy_no_warnings():
    r = analyze_performance({"fps": 60, "nodes": [
        {"name": "/p/a", "cook_time": 0.5, "cpu": 10, "gpu": 5}]})
    assert r["fps_ok"] is True
    assert "healthy" in r["suggestions"][0].lower()
