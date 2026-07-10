"""Tests for td_mcp.glsl_patterns (named shaders + network templates)."""

from td_mcp.glsl_patterns import (
    get_glsl_pattern,
    get_network_template,
    list_glsl_patterns,
    list_network_templates,
)


def test_glsl_patterns_present():
    names = list_glsl_patterns()
    for n in ("simple_noise", "rgb_shift", "hue_cycle", "feedback_blend",
              "kaleidoscope", "scanline"):
        assert n in names


def test_get_glsl_pattern_has_fragment():
    p = get_glsl_pattern("rgb_shift")
    assert "fragment" in p and "description" in p
    assert "texture(" in p["fragment"]


def test_unknown_glsl_pattern_errors():
    p = get_glsl_pattern("nope")
    assert "error" in p
    assert "available" in p


def test_network_templates_present():
    names = list_network_templates()
    for n in ("audio_reactive", "feedback", "render_scene", "led_wall"):
        assert n in names


def test_get_template_operators():
    t = get_network_template("render_scene")
    types = {o["type"] for o in t["operators"]}
    assert "Geometry COMP" in types
    assert "Render TOP" in types
    # Wiring is self-consistent (inputs reference earlier names / None).
    names = {o["name"] for o in t["operators"]}
    for o in t["operators"]:
        for src in o["inputs"]:
            assert src is None or src in names
