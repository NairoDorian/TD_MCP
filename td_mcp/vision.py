"""Viewport vision — deterministic pixel analysis + caption (tdmcp captionTop).

`capture_viewport` in the bridge can already return a pass/black/flat verdict,
but a *blind* agent still cannot "see" the render. This module computes the
same verdict deterministically from raw RGBA pixels (no vision LLM required)
and adds tdmcp's ``captionTop`` extras: a short text caption plus pixel stats
(``mean_luma``, ``near_black_fraction``, ``saturation_mean``, ``classification``)
that work as a histogram fallback when no vision model is configured.

Run:  uv run python -m tests.test_vision
"""

from __future__ import annotations

import struct
from typing import Any, Dict, List, Tuple


def analyze_pixels(pixels: bytes, width: int, height: int) -> Dict[str, Any]:
    """Analyze raw RGBA8888 pixels and return verdict + stats.

    ``pixels`` is ``width*height*4`` bytes (R,G,B,A per pixel). Returns a
    histogram-derived summary an agent (or a vision LLM) can reason about.
    """
    n = width * height
    if n == 0 or len(pixels) < n * 4:
        return {"error": "empty or short pixel buffer", "classification": "unknown"}

    luma_sum = 0.0
    sat_sum = 0.0
    near_black = 0
    transparent = 0
    # Sample down for speed on large buffers.
    step = max(1, n // 4000)
    sampled = 0
    prev = None
    flat = True
    for i in range(0, n, step):
        o = i * 4
        r, g, b, a = pixels[o], pixels[o + 1], pixels[o + 2], pixels[o + 3]
        luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
        luma_sum += luma
        mx, mn = max(r, g, b), min(r, g, b)
        sat_sum += (mx - mn)
        if luma < 12:
            near_black += 1
        if a < 24:
            transparent += 1
        if prev is not None and abs(luma - prev) > 8:
            flat = False
        prev = luma
        sampled += 1

    mean_luma = luma_sum / sampled if sampled else 0.0
    saturation_mean = (sat_sum / sampled) if sampled else 0.0
    near_black_fraction = near_black / sampled if sampled else 0.0
    transparent_fraction = transparent / sampled if sampled else 0.0

    is_black = near_black_fraction > 0.97
    fully_transparent = transparent_fraction > 0.97
    is_flat = flat and saturation_mean < 12 and not is_black

    classification = "black" if is_black else (
        "transparent" if fully_transparent else (
            "flat" if is_flat else (
                "colorful" if saturation_mean > 40 else "normal")))

    return {
        "width": width, "height": height, "sampled": sampled,
        "mean_luma": round(mean_luma, 2),
        "saturation_mean": round(saturation_mean, 2),
        "near_black_fraction": round(near_black_fraction, 4),
        "transparent_fraction": round(transparent_fraction, 4),
        "is_black": is_black,
        "is_flat": is_flat,
        "fully_transparent": fully_transparent,
        "classification": classification,
    }


def caption_from_stats(stats: Dict[str, Any]) -> str:
    """Turn pixel stats into a one-line human/vision caption."""
    if stats.get("error"):
        return "could not analyze viewport"
    c = stats.get("classification", "unknown")
    if c == "black":
        return "Viewport is black — no visible output (likely an unconnected or mis-wired node)."
    if c == "transparent":
        return "Viewport is fully transparent — alpha is zero across the frame."
    if c == "flat":
        return f"Viewport is flat/uniform (mean_luma={stats['mean_luma']}) — a single solid color, no content."
    if c == "colorful":
        return f"Viewport is colorful (sat={stats['saturation_mean']}, luma={stats['mean_luma']}) — populated render."
    return f"Viewport looks normal (luma={stats['mean_luma']}, sat={stats['saturation_mean']})."


def viewport_verdict(pixels: bytes, width: int, height: int,
                     use_vision: bool = False) -> Dict[str, Any]:
    """Bridge-facing verdict combining deterministic stats with an optional
    vision flag (the agent supplies the vision caption separately)."""
    stats = analyze_pixels(pixels, width, height)
    verdict = {
        "pass": stats.get("classification") in ("normal", "colorful"),
        "is_black": stats.get("is_black", False),
        "is_flat": stats.get("is_flat", False),
        "fully_transparent": stats.get("fully_transparent", False),
        "stats": stats,
        "caption": caption_from_stats(stats),
    }
    fail_reasons: List[str] = []
    if verdict["is_black"]:
        fail_reasons.append("black")
    if verdict["is_flat"]:
        fail_reasons.append("flat")
    if verdict["fully_transparent"]:
        fail_reasons.append("transparent")
    verdict["fail_reasons"] = fail_reasons
    if use_vision:
        verdict["use_vision"] = True
    return verdict


def make_test_image(kind: str, w: int = 16, h: int = 16) -> Tuple[bytes, int, int]:
    """Synthetic RGBA image generator for tests/benchmarks."""
    buf = bytearray()
    for y in range(h):
        for x in range(w):
            if kind == "black":
                buf += bytes((0, 0, 0, 255))
            elif kind == "colorful":
                buf += bytes(((x * 16) % 256, (y * 16) % 256, 128, 255))
            elif kind == "flat":
                buf += bytes((120, 120, 120, 255))
            elif kind == "transparent":
                buf += bytes((10, 10, 10, 0))
            else:
                buf += bytes((200, 200, 200, 255))
    return bytes(buf), w, h
