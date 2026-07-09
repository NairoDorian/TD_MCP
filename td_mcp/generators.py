"""Artist generators (tdmcp Layer 1 style) — opinionated network builders.

Each generator returns a list of operator specs compatible with `td_build_network`
and the live `build_and_verify` loop. They include:
  - create_feedback_network
  - create_audio_reactive
  - create_particle_system
  - create_3d_scene
  - create_glsl_shader
  - create_led_wall
  - create_dmx_fixture_pipeline
  - create_video_pipeline
  - create_midi_rig
  - create_kinect_skeleton
"""

import json
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _op_spec(op_type: str, name: str, params=None, inputs=None, position=None):
    """Compact spec for td_build_network / TDN."""
    spec = {"type": op_type, "name": name}
    if params:
        spec["params"] = params
    if inputs:
        spec["inputs"] = inputs
    if position:
        spec["position"] = position
    return spec



def _grid_pos(index, cols=3, spacing=200):
    """Layout helper: arrange in grid rows."""
    row = index // cols
    col = index % cols
    return [col * spacing, row * spacing]


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------
def create_feedback_network(name="feedback1", decay=0.95, iterations=3, params=None):
    """Classic feedback loop: Noise -> Level (decay) -> Feedback -> Level/Threshold -> Out.

    Args:
        name: base name prefix
        decay: Level TOP post value (0..1), lower = longer trails
        iterations: number of feedback stages (1..4)
        params: optional dict of extra param overrides
    """
    specs = []
    base = name
    # Stage 0: noise source
    specs.append(_op_spec("Noise TOP", f"{base}_noise",
                          params={"type": "Random", "amplitude": 1.0},
                          position=_grid_pos(0)))
    last = f"{base}_noise"

    for i in range(iterations):
        # Level (decay)
        lvl = _op_spec("Level TOP", f"{base}_lvl{i}",
                       params={"post": decay, "gamma": 1.0},
                       inputs=[last],
                       position=_grid_pos(1 + i * 2))
        specs.append(lvl)
        # Feedback
        fb = _op_spec("Feedback TOP", f"{base}_fb{i}",
                      inputs=[lvl["name"], lvl["name"]],  # self-feed
                      position=_grid_pos(2 + i * 2))
        specs.append(fb)
        last = fb["name"]

    # Final glow
    thr = _op_spec("Threshold TOP", f"{base}_thr",
                   params={"threshold": 0.1, "invert": False},
                   inputs=[last],
                   position=_grid_pos(2 + iterations * 2))
    specs.append(thr)

    out = _op_spec("Out TOP", f"{base}_out",
                   inputs=[thr["name"]],
                   position=_grid_pos(3 + iterations * 2))
    specs.append(out)

    return specs


def create_audio_reactive(name="audio1", chop_source="Audio Device In CHOP",
                          freq_band=(0, 100), target_param="radius",
                          target_type="Circle TOP", params=None):
    """Audio-reactive: AudioDeviceIn -> Math (normalize) -> Filter -> Export to target param.

    Returns a spec list PLUS a CHOP export instruction (handled by build_and_verify).
    """
    specs = []
    base = name
    # Audio input
    specs.append(_op_spec(chop_source, f"{base}_audio",
                          params={"device": "default", "channels": 2},
                          position=_grid_pos(0)))
    # Math CHOP normalize
    specs.append(_op_spec("Math CHOP", f"{base}_math",
                          params={"from_range": [-1, 1], "to_range": [0, 1]},
                          inputs=[f"{base}_audio"],
                          position=_grid_pos(1)))
    # Filter CHOP smooth
    specs.append(_op_spec("Filter CHOP", f"{base}_filter",
                          params={"filter": "One-Pole", "cutoff": 0.1},
                          inputs=[f"{base}_math"],
                          position=_grid_pos(2)))

    # Visual target
    specs.append(_op_spec(target_type, f"{base}_vis",
                          params={target_param: 0.5} if target_type == "Circle TOP" else {},
                          position=_grid_pos(3)))

    # Export instruction (handled client-side via set_parameters with export flag)
    export_note = {
        "export": True,
        "from": f"{base}_filter:chan0",
        "to": f"{base}_vis.{target_param}",
        "note": "Export CHOP channel to visual parameter"
    }
    return {"specs": specs, "exports": [export_note]}


def create_particle_system(name="particles1", count=1000, use_pops=True, params=None):
    """GPU particle system: POP (or Particle GPU TOP) -> Point Sprite / Billboard -> Render.

    Args:
        use_pops: True = POP network (2023.10000+), False = Particle GPU TOP (older)
    """
    specs = []
    base = name

    if use_pops:
        # POP network
        specs.append(_op_spec("POP Location", f"{base}_loc",
                              params={"generate": "Random", "count": count},
                              position=_grid_pos(0)))
        specs.append(_op_spec("POP Force", f"{base}_force",
                              params={"force_type": "Gravity", "strength": 0.01},
                              inputs=[f"{base}_loc"],
                              position=_grid_pos(1)))
        specs.append(_op_spec("POP Solver", f"{base}_solver",
                              params={"life": 100, "friction": 0.99},
                              inputs=[f"{base}_force"],
                              position=_grid_pos(2)))
        specs.append(_op_spec("POP Surface", f"{base}_surf",
                              inputs=[f"{base}_solver"],
                              position=_grid_pos(3)))
        render_input = f"{base}_surf"
    else:
        # Particle GPU TOP
        specs.append(_op_spec("Particle GPU TOP", f"{base}_pgpu",
                              params={"number": count, "life": 100, "force": 0.01},
                              position=_grid_pos(0)))
        specs.append(_op_spec("Point Sprite TOP", f"{base}_sprite",
                              inputs=[f"{base}_pgpu"],
                              position=_grid_pos(1)))
        render_input = f"{base}_sprite"

    # Render chain
    specs.append(_op_spec("Geometry COMP", f"{base}_geo",
                          inputs=[render_input],
                          position=_grid_pos(4)))
    specs.append(_op_spec("Camera COMP", f"{base}_cam",
                          position=_grid_pos(5)))
    specs.append(_op_spec("Light COMP", f"{base}_light",
                          position=_grid_pos(6)))
    specs.append(_op_spec("Render TOP", f"{base}_render",
                          params={"camera": f"{base}_cam", "light": f"{base}_light"},
                          inputs=[f"{base}_geo"],
                          position=_grid_pos(7)))
    specs.append(_op_spec("Out TOP", f"{base}_out",
                          inputs=[f"{base}_render"],
                          position=_grid_pos(8)))

    return specs


def create_3d_scene(name="scene1", complexity="simple", params=None):
    """Basic 3D scene: Geometry -> Material -> Render with Camera/Light.

    complexity: 'simple' (Box+Phong) | 'pbr' (PBR MAT + Env Light) | 'instanced' (instancing)
    """
    specs = []
    base = name

    if complexity == "instanced":
        # Instanced grid of boxes
        specs.append(_op_spec("Box SOP", f"{base}_box", position=_grid_pos(0)))
        specs.append(_op_spec("Noise CHOP", f"{base}_noise",
                              params={"type": "Random", "amplitude": 0.5},
                              position=_grid_pos(1)))
        specs.append(_op_spec("CHOP to TOP", f"{base}_c2t",
                              inputs=[f"{base}_noise"],
                              position=_grid_pos(2)))
        specs.append(_op_spec("Geometry COMP", f"{base}_geo",
                              params={"instance": f"{base}_c2t"},
                              inputs=[f"{base}_box"],
                              position=_grid_pos(3)))
    else:
        # Single geo
        sop_type = "Box SOP" if complexity != "pbr" else "Torus SOP"
        specs.append(_op_spec(sop_type, f"{base}_sop", position=_grid_pos(0)))

        mat_type = "PBR MAT" if complexity == "pbr" else "Phong MAT"
        if mat_type == "Phong MAT":
            mat_params = {"color": [1, 0.8, 0.2]}
        else:
            mat_params = {"base_color": [1, 0.8, 0.2], "metallic": 0.5, "roughness": 0.3}
        specs.append(_op_spec(mat_type, f"{base}_mat",
                              params=mat_params,
                              position=_grid_pos(1)))

        specs.append(_op_spec("Geometry COMP", f"{base}_geo",
                              params={"material": f"{base}_mat"},
                              inputs=[f"{base}_sop"],
                              position=_grid_pos(2)))

    # Camera + Light
    specs.append(_op_spec("Camera COMP", f"{base}_cam",
                          params={"position": [0, 0, 10]},
                          position=_grid_pos(3)))
    if complexity == "pbr":
        specs.append(_op_spec("Environment Light COMP", f"{base}_envlight",
                              params={"intensity": 1.0},
                              position=_grid_pos(4)))
    else:
        specs.append(_op_spec("Light COMP", f"{base}_light",
                              params={"light_type": "Point", "color": [1, 1, 1]},
                              position=_grid_pos(4)))

    # Render
    specs.append(_op_spec("Render TOP", f"{base}_render",
                          params={"camera": f"{base}_cam", "light": f"{base}_light" if complexity != "pbr" else f"{base}_envlight"},
                          inputs=[f"{base}_geo"],
                          position=_grid_pos(5)))
    specs.append(_op_spec("Out TOP", f"{base}_out",
                          inputs=[f"{base}_render"],
                          position=_grid_pos(6)))

    return specs
def create_glsl_shader(name="glsl1", template="basic", params=None):
    """GLSL TOP with a ready-to-tweak shader template.

    template: 'basic' | 'sdf' | 'feedback' | 'audio' | 'custom'
    """
    templates = {
        "basic": """
uniform sampler2D sTD2DInputs[4];
out vec4 fragColor;
void main() {
    vec2 uv = vUV.st;
    vec4 c = texture(sTD2DInputs[0], uv);
    fragColor = vec4(c.rgb * 1.2, c.a);
}
""",
        "sdf": """
uniform sampler2D sTD2DInputs[4];
uniform float uTime;
out vec4 fragColor;
float sdCircle(vec2 p, float r) { return length(p) - r; }
void main() {
    vec2 uv = (vUV.st - 0.5) * 2.0;
    float d = sdCircle(uv, 0.3 + 0.5 * sin(uTime));
    float a = smoothstep(0.02, 0.0, abs(d));
    fragColor = vec4(vec3(a), a);
}
""",
        "feedback": """
uniform sampler2D sTD2DInputs[4];
uniform float uFeedback;
out vec4 fragColor;
void main() {
    vec2 uv = vUV.st;
    vec4 prev = texture(sTD2DInputs[0], uv);
    vec4 curr = texture(sTD2DInputs[1], uv);
    fragColor = mix(curr, prev, uFeedback);
}
""",
    }

    specs = []
    base = name
    shader = templates.get(template, templates["basic"])

    specs.append(_op_spec("GLSL TOP", f"{base}_glsl",
                          params={"pixel_shader": shader, "custom_uniforms": "uFeedback"},
                          position=_grid_pos(0)))

    specs.append(_op_spec("Out TOP", f"{base}_out",
                          inputs=[f"{base}_glsl"],
                          position=_grid_pos(1)))

    return specs


def create_led_wall(name="ledwall1", width=16, height=16, protocol="art-net", params=None):
    """Create a pixel-mapping pipeline for an LED wall.

    Noise TOP (Source) -> Resolution TOP (Scale to width x height) -> TOP to CHOP (Extract pixel channels) -> DMX Out CHOP (Send via DMX)
    """
    specs = []
    base = name

    # 1. Visual source (Noise TOP)
    specs.append(_op_spec("Noise TOP", f"{base}_source",
                          params={"type": "Random", "amplitude": 1.0},
                          position=_grid_pos(0)))

    # 2. Downscale to pixel grid dimensions (Resolution TOP)
    specs.append(_op_spec("Resolution TOP", f"{base}_scale",
                          params={"outputresolution": "custom", "resolutionw": width, "resolutionh": height},
                          inputs=[f"{base}_source"],
                          position=_grid_pos(1)))

    # 3. Extract RGB values (TOP to CHOP)
    specs.append(_op_spec("TOP to CHOP", f"{base}_topto",
                          params={"top": f"{base}_scale", "rgball": "rgb"},
                          position=_grid_pos(2)))

    # 4. DMX output (DMX Out CHOP)
    dmx_format = "artnet" if protocol.lower() == "art-net" else "sacn"
    specs.append(_op_spec("DMX Out CHOP", f"{base}_dmx",
                          params={"format": dmx_format, "universe": 1},
                          inputs=[f"{base}_topto"],
                          position=_grid_pos(3)))

    return specs


def create_dmx_fixture_pipeline(name="dmx_fixture1", channels="chan1 chan2 chan3", protocol="sacn", params=None):
    """Create a DMX input pipeline to receive sACN/Art-Net control channels.

    DMX In CHOP -> Select CHOP (choose channels) -> Math CHOP (map range) -> Out CHOP
    """
    specs = []
    base = name

    # 1. DMX Input
    dmx_format = "sacn" if protocol.lower() == "sacn" else "artnet"
    specs.append(_op_spec("DMX In CHOP", f"{base}_in",
                          params={"format": dmx_format, "universe": 1},
                          position=_grid_pos(0)))

    # 2. Channel Select
    specs.append(_op_spec("Select CHOP", f"{base}_select",
                          params={"channames": channels},
                          inputs=[f"{base}_in"],
                          position=_grid_pos(1)))

    # 3. Normalize range (0-255 DMX -> 0-1 TD range)
    specs.append(_op_spec("Math CHOP", f"{base}_math",
                          params={"from_range": [0.0, 255.0], "to_range": [0.0, 1.0]},
                          inputs=[f"{base}_select"],
                          position=_grid_pos(2)))

    # 4. Out
    specs.append(_op_spec("Out CHOP", f"{base}_out",
                          inputs=[f"{base}_math"],
                          position=_grid_pos(3)))

    return specs


def create_video_pipeline(name="vidpipe1", source_file="", apply_lut=True, apply_chromakey=False, params=None):
    """Create a video playback and processing pipeline.

    MovieFileIn -> (optional) Chroma Key TOP -> LUT TOP -> Level TOP -> Out TOP
    Args:
        source_file: Path to the movie file (can be set later)
        apply_lut: Include a LUT TOP for colour grading
        apply_chromakey: Include a Chroma Key TOP for green-screen removal
    """
    specs = []
    base = name
    last = f"{base}_src"

    specs.append(_op_spec("Movie File In TOP", f"{base}_src",
                          params={"file": source_file, "play": True, "loop": True},
                          position=_grid_pos(0)))

    if apply_chromakey:
        specs.append(_op_spec("Chroma Key TOP", f"{base}_ckey",
                              params={"keycolor": [0, 1, 0], "tolerance": 0.3},
                              inputs=[last],
                              position=_grid_pos(1)))
        last = f"{base}_ckey"

    if apply_lut:
        specs.append(_op_spec("LUT TOP", f"{base}_lut",
                              params={"luttype": "Film"},
                              inputs=[last],
                              position=_grid_pos(2 if apply_chromakey else 1)))
        last = f"{base}_lut"

    specs.append(_op_spec("Level TOP", f"{base}_lvl",
                          params={"contrast": 1.05, "brightness": 0.0},
                          inputs=[last],
                          position=_grid_pos(3 if apply_lut else 1)))
    last = f"{base}_lvl"

    specs.append(_op_spec("Out TOP", f"{base}_out",
                          inputs=[last],
                          position=_grid_pos(4 if apply_lut else 2)))
    return specs


def create_midi_rig(name="midi1", device_id=0, params=None):
    """Create a MIDI input rig: MIDI In CHOP -> Select CHOP -> Math CHOP (normalize) -> Out CHOP.

    The normalized output can be exported to any parameter via CHOP export.
    """
    specs = []
    base = name

    specs.append(_op_spec("MIDI In CHOP", f"{base}_in",
                          params={"device": device_id, "realtime": True},
                          position=_grid_pos(0)))

    specs.append(_op_spec("Select CHOP", f"{base}_sel",
                          params={"chop": f"{base}_in", "channames": "*"},
                          inputs=[f"{base}_in"],
                          position=_grid_pos(1)))

    specs.append(_op_spec("Math CHOP", f"{base}_norm",
                          params={"from_range": [0.0, 127.0], "to_range": [0.0, 1.0]},
                          inputs=[f"{base}_sel"],
                          position=_grid_pos(2)))

    specs.append(_op_spec("Filter CHOP", f"{base}_filt",
                          params={"filter": "One-Pole", "cutoff": 0.15},
                          inputs=[f"{base}_norm"],
                          position=_grid_pos(3)))

    specs.append(_op_spec("Out CHOP", f"{base}_out",
                          inputs=[f"{base}_filt"],
                          position=_grid_pos(4)))
    return specs


def create_kinect_skeleton(name="kinect1", tracked_joints=None, visualize=True, params=None):
    """Create a Kinect Azure skeleton tracking rig.

    Kinect Azure TOP/CHOP -> Body Track TOP -> SOP Conversion -> (optional) Point Sprite visualization
    Args:
        tracked_joints: Optional list of joint names to select (e.g. ['head', 'handleft'])
        visualize:       Add a Point Sprite TOP for skeleton visualisation
    """
    specs = []
    base = name
    joints = tracked_joints or ["head", "shoulderleft", "shoulderright",
                                  "handleft", "handright", "hipleft", "hipright"]

    # 1. Kinect Azure CHOP (joint positions)
    specs.append(_op_spec("Kinect Azure CHOP", f"{base}_joints",
                          params={"datatype": "SkeletonJoint", "active": True},
                          position=_grid_pos(0)))

    # 2. Select CHOP (pick the joints we care about)
    specs.append(_op_spec("Select CHOP", f"{base}_sel",
                          params={"channames": " ".join(joints)},
                          inputs=[f"{base}_joints"],
                          position=_grid_pos(1)))

    # 3. Math CHOP remap (-1..1 to 0..1)
    specs.append(_op_spec("Math CHOP", f"{base}_remap",
                          params={"from_range": [-1.0, 1.0], "to_range": [0.0, 1.0]},
                          inputs=[f"{base}_sel"],
                          position=_grid_pos(2)))

    if visualize:
        # CHOP to TOP -> Point Sprite TOP for skeleton overlay
        specs.append(_op_spec("CHOP to TOP", f"{base}_c2t",
                              params={"chop": f"{base}_remap"},
                              inputs=[f"{base}_remap"],
                              position=_grid_pos(3)))
        specs.append(_op_spec("Point Sprite TOP", f"{base}_pts",
                              params={"pointsize": 8.0},
                              inputs=[f"{base}_c2t"],
                              position=_grid_pos(4)))
        specs.append(_op_spec("Out TOP", f"{base}_out",
                              inputs=[f"{base}_pts"],
                              position=_grid_pos(5)))
    else:
        specs.append(_op_spec("Out CHOP", f"{base}_out",
                              inputs=[f"{base}_remap"],
                              position=_grid_pos(3)))

    return specs


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
GENERATORS = {
    "feedback": create_feedback_network,
    "audio_reactive": create_audio_reactive,
    "particles": create_particle_system,
    "3d_scene": create_3d_scene,
    "glsl": create_glsl_shader,
    "led_wall": create_led_wall,
    "dmx_fixture": create_dmx_fixture_pipeline,
    "video_pipeline": create_video_pipeline,
    "midi_rig": create_midi_rig,
    "kinect_skeleton": create_kinect_skeleton,
}


def list_generators():
    return list(GENERATORS.keys())

def generate(name, **kwargs):
    fn = GENERATORS.get(name)
    if not fn:
        raise ValueError(f"Unknown generator: {name}. Available: {list(GENERATORS.keys())}")
    return fn(**kwargs)