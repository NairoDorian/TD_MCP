"""Tests for showcontrol media-server connectors + offline tool wiring."""

import json

from td_mcp import showcontrol as sc
from td_mcp.server_offline import (
    td_analyze_performance,
    td_build_audio_reactive,
    td_compat_check,
    td_expert_prompt,
    td_glsl_pattern,
    td_mediaserver,
    td_network_template,
    td_score_build,
    td_self_heal,
    td_validate_build,
    td_build_video_pipeline,
    td_build_midi_rig,
    td_build_kinect_skeleton,
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


def test_td_build_audio_reactive_returns_yaml():
    out = td_build_audio_reactive("{}")
    assert "EXPORTS" in out  # CHOP export note preserved
    assert "operator" in out.lower() or "CHOP" in out


def test_td_self_heal_and_validate_build_tools():
    spec = "operators:\n  - name: x\n    type: null\n"
    v = json.loads(td_validate_build(spec))
    assert "validation" in v and "score" in v
    h = json.loads(td_self_heal(spec))
    assert "fixed_desc" in h and "iterations" in h


def test_td_build_video_pipeline_tool():
    out = td_build_video_pipeline("{}")
    assert "Movie File In TOP" in out and "Out TOP" in out


def test_td_build_midi_rig_tool():
    out = td_build_midi_rig("{}")
    assert "MIDI In CHOP" in out and "Out CHOP" in out


def test_td_build_kinect_skeleton_tool():
    out = td_build_kinect_skeleton("{}")
    assert "Kinect Azure CHOP" in out
