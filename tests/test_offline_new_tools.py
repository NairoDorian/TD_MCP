"""Tests for showcontrol media-server connectors + offline tool wiring."""

from td_mcp import showcontrol as sc
from td_mcp.server_offline import (
    td_analyze_performance,
    td_compat_check,
    td_expert_prompt,
    td_glsl_pattern,
    td_mediaserver,
    td_network_template,
    td_score_build,
)


def test_mediaserver_plan():
    r = sc.media_server("resolume")
    assert r["transport"] == "osc"
    assert r["operator"] == "OSC Out DAT"
    bad = sc.media_server("ghost")
    assert "error" in bad


def test_td_glsl_pattern_tool():
    out = td_glsl_pattern("rgb_shift")
    assert "fragment" in out


def test_td_network_template_tool():
    out = td_network_template("render_scene")
    assert "Geometry COMP" in out


def test_td_expert_prompt_tool():
    out = td_expert_prompt(phase="build")
    assert "phase: build" in out


def test_td_compat_check_tool():
    out = td_compat_check("2023.10000", "2024.10000")
    assert "error" in out


def test_td_score_build_tool():
    spec = '[{"name":"n","type":"Noise TOP","inputs":[null]},' \
           '{"name":"l","type":"Level TOP","inputs":["n"]}]'
    out = td_score_build(spec)
    assert '"grade"' in out


def test_td_mediaserver_tool():
    out = td_mediaserver("notch")
    assert "spout" in out.lower() or "transport" in out.lower()


def test_td_analyze_performance_tool():
    out = td_analyze_performance('{"fps": 10, "nodes": []}')
    assert "fps_ok" in out
