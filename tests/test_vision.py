"""Tests for td_mcp.vision (deterministic viewport analysis + caption)."""

from td_mcp.vision import (
    analyze_pixels,
    caption_from_stats,
    make_test_image,
    viewport_verdict,
)


def test_black_image():
    px, w, h = make_test_image("black")
    s = analyze_pixels(px, w, h)
    assert s["is_black"] is True
    assert s["classification"] == "black"


def test_colorful_image():
    px, w, h = make_test_image("colorful")
    s = analyze_pixels(px, w, h)
    assert s["classification"] in ("colorful", "normal")
    assert s["saturation_mean"] > 10


def test_flat_image():
    px, w, h = make_test_image("flat")
    s = analyze_pixels(px, w, h)
    assert s["is_flat"] is True


def test_transparent_image():
    px, w, h = make_test_image("transparent")
    s = analyze_pixels(px, w, h)
    assert s["fully_transparent"] is True


def test_verdict_pass():
    px, w, h = make_test_image("colorful")
    v = viewport_verdict(px, w, h)
    assert v["pass"] is True
    assert "black" not in v["fail_reasons"]
    assert v["caption"]


def test_caption_text():
    px, w, h = make_test_image("black")
    s = analyze_pixels(px, w, h)
    assert "black" in caption_from_stats(s).lower()
