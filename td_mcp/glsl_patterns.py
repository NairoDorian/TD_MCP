"""Named GLSL patterns + ready-to-paste network templates.

Two "knowledge enrichment" ideas from bottobot's doc server: a library of
named, paste-ready GLSL fragment shaders (``get_glsl_pattern``) and
ready-to-build network templates (``get_network_template``). These emit the
same TDN operator lists ``td_build_network`` consumes, so an agent can drop in
a known-good pattern instead of generating from scratch.

Run:  uv run python -m tests.test_glsl
"""

from __future__ import annotations

from typing import Dict, List

# --- Named GLSL fragment shaders (paste into a GLSL TOP) ---------------------
GLSL_PATTERNS: Dict[str, Dict[str, str]] = {
    "simple_noise": {
        "description": "Animated value noise producing a grayscale field.",
        "fragment": """
out vec4 fragColor;
void main() {
  vec2 uv = vUV.st;
  float n = fract(sin(dot(uv, vec2(12.9898, 78.233))) * 43758.5453);
  fragColor = vec4(vec3(n), 1.0);
}
""",
    },
    "rgb_shift": {
        "description": "Chromatic aberration: offset R/G/B sampling.",
        "fragment": """
uniform sampler2D sTD2DInputs[1];
out vec4 fragColor;
void main() {
  vec2 uv = vUV.st;
  float r = texture(sTD2DInputs[0], uv + vec2(0.01, 0.0)).r;
  float g = texture(sTD2DInputs[0], uv).g;
  float b = texture(sTD2DInputs[0], uv - vec2(0.01, 0.0)).b;
  fragColor = vec4(r, g, b, 1.0);
}
""",
    },
    "hue_cycle": {
        "description": "Rotate hue over time using uTime.",
        "fragment": """
uniform float uTime;
out vec4 fragColor;
vec3 hue(float h){ return clamp(abs(mod(h*6.0+vec3(0,4,2),6.0)-3.0)-1.0,0.0,1.0); }
void main() {
  vec3 c = hue(fract(uTime * 0.1 + vUV.st.x));
  fragColor = vec4(c, 1.0);
}
""",
    },
    "feedback_blend": {
        "description": "Blend current frame with previous (feedback source).",
        "fragment": """
uniform sampler2D sTD2DInputs[2];
out vec4 fragColor;
void main() {
  vec4 cur = texture(sTD2DInputs[0], vUV.st);
  vec4 prev = texture(sTD2DInputs[1], vUV.st);
  fragColor = mix(cur, prev, 0.85);
}
""",
    },
    "kaleidoscope": {
        "description": "Mirror the UVs around the center for a kaleidoscope.",
        "fragment": """
uniform sampler2D sTD2DInputs[1];
out vec4 fragColor;
void main() {
  vec2 uv = vUV.st * 2.0 - 1.0;
  float a = atan(uv.y, uv.x);
  float r = length(uv);
  a = abs(mod(a, 1.0472) - 0.5236);
  uv = vec2(cos(a), sin(a)) * r;
  fragColor = texture(sTD2DInputs[0], uv * 0.5 + 0.5);
}
""",
    },
    "scanline": {
        "description": "CRT-style horizontal scanlines over the input.",
        "fragment": """
uniform sampler2D sTD2DInputs[1];
out vec4 fragColor;
void main() {
  vec4 c = texture(sTD2DInputs[0], vUV.st);
  float s = 0.85 + 0.15 * sin(vUV.st.y * 800.0);
  fragColor = vec4(c.rgb * s, 1.0);
}
""",
    },
}

# --- Ready-to-build network templates (operator lists) ---------------------
NETWORK_TEMPLATES: Dict[str, Dict[str, object]] = {
    "audio_reactive": {
        "description": "Audio Device In -> Beat/Filter -> Level -> Out",
        "operators": [
            {"name": "audio_in", "type": "Audio Device In CHOP", "inputs": [None]},
            {"name": "filter", "type": "Filter CHOP", "inputs": ["audio_in"]},
            {"name": "level", "type": "Level CHOP", "inputs": ["filter"]},
            {"name": "null", "type": "Null CHOP", "inputs": ["level"]},
        ],
    },
    "feedback": {
        "description": "Noise -> Level -> Feedback -> Level -> Out",
        "operators": [
            {"name": "noise", "type": "Noise TOP", "inputs": [None]},
            {"name": "level", "type": "Level TOP", "inputs": ["noise"]},
            {"name": "feedback", "type": "Feedback TOP", "inputs": ["level"]},
            {"name": "out", "type": "Null TOP", "inputs": ["feedback"]},
        ],
    },
    "render_scene": {
        "description": "Geometry + Camera + Light -> Render -> Out",
        "operators": [
            {"name": "geo", "type": "Geometry COMP", "inputs": [None]},
            {"name": "cam", "type": "Camera COMP", "inputs": [None]},
            {"name": "light", "type": "Light COMP", "inputs": [None]},
            {"name": "render", "type": "Render TOP",
             "inputs": ["geo", "cam", "light"]},
            {"name": "out", "type": "Null TOP", "inputs": ["render"]},
        ],
    },
    "led_wall": {
        "description": "Noise -> Resolution -> TOP to CHOP -> DMX Out",
        "operators": [
            {"name": "noise", "type": "Noise TOP", "inputs": [None]},
            {"name": "res", "type": "Resolution TOP", "inputs": ["noise"]},
            {"name": "t2c", "type": "TOP to CHOP", "inputs": ["res"]},
            {"name": "dmx", "type": "DMX Out CHOP", "inputs": ["t2c"]},
        ],
    },
}

# Suggested connections derived from each operator's `inputs` (positional).
CONNECTION_FAMILY = {
    "CHOP": "CHOP", "TOP": "TOP", "SOP": "SOP", "DAT": "DAT", "POP": "POP", "COMP": "COMP",
}


def list_glsl_patterns() -> List[str]:
    return sorted(GLSL_PATTERNS)


def get_glsl_pattern(name: str) -> Dict[str, str]:
    if name not in GLSL_PATTERNS:
        return {"error": f"unknown GLSL pattern {name!r}",
                "available": list_glsl_patterns()}
    return {"name": name, **GLSL_PATTERNS[name]}


def list_network_templates() -> List[str]:
    return sorted(NETWORK_TEMPLATES)


def get_network_template(name: str) -> Dict[str, object]:
    if name not in NETWORK_TEMPLATES:
        return {"error": f"unknown template {name!r}",
                "available": list_network_templates()}
    return {"name": name, **NETWORK_TEMPLATES[name]}
