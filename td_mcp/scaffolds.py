"""Project scaffolds (ai-brain `brain_vj_builder.py` idea).

Produces opinionated TDN network blueprints for common TouchDesigner show/VJ
projects: audio input, audio analysis, control bus, scene bank, FX rack, mixer,
mapper, monitor, and a safety section. These are *planners*, not live nodes —
the same diffable YAML the rest of the offline server emits.

Pure stdlib + the existing TDN layer.
"""

from __future__ import annotations

from typing import Any, Dict, List

SCAFFOLDS: Dict[str, Dict[str, Any]] = {
    "vj": {
        "description": "VJ show rig: audio in -> analysis -> scene bank -> FX rack -> mixer -> monitor.",
        "sections": [
            "audio_in", "audio_analysis", "control_bus", "scene_bank",
            "fx_rack", "mixer", "mapper", "monitor", "safety",
        ],
        "operators": [
            {"name": "audio_in", "type": "Audio Device In CHOP", "inputs": [None]},
            {"name": "audio_spectrum", "type": "Audio Spectrum CHOP", "inputs": ["audio_in"]},
            {"name": "audio_filter", "type": "Filter CHOP", "inputs": ["audio_spectrum"]},
            {"name": "beat", "type": "Beat CHOP", "inputs": ["audio_in"]},
            {"name": "control", "type": "Constant CHOP", "inputs": [None]},
            {"name": "scene_a", "type": "Movie File In TOP", "inputs": [None]},
            {"name": "scene_b", "type": "Movie File In TOP", "inputs": [None]},
            {"name": "scene_switch", "type": "Switch TOP", "inputs": ["scene_a", "scene_b"]},
            {"name": "fx_level", "type": "Level TOP", "inputs": ["scene_switch"]},
            {"name": "fx_blur", "type": "Blur TOP", "inputs": ["fx_level"]},
            {"name": "fx_feedback", "type": "Feedback TOP", "inputs": ["fx_blur"]},
            {"name": "mix", "type": "Mix TOP", "inputs": ["fx_feedback", "scene_switch"]},
            {"name": "mapper", "type": "Fit TOP", "inputs": ["mix"]},
            {"name": "monitor", "type": "Null TOP", "inputs": ["mapper"]},
            {"name": "out", "type": "Out TOP", "inputs": ["monitor"]},
        ],
    },
    "audio_reactive": {
        "description": "Minimal audio-reactive chain: audio -> normalize -> export to visuals.",
        "sections": ["audio_in", "audio_analysis", "control_bus", "monitor", "safety"],
        "operators": [
            {"name": "audio_in", "type": "Audio Device In CHOP", "inputs": [None]},
            {"name": "math", "type": "Math CHOP", "inputs": ["audio_in"]},
            {"name": "filter", "type": "Filter CHOP", "inputs": ["math"]},
            {"name": "visual", "type": "Noise TOP", "inputs": [None]},
            {"name": "out", "type": "Null TOP", "inputs": ["visual"]},
        ],
    },
    "installation": {
        "description": "Installation-safe rig: OSC input -> scene switch -> safe blackout -> out.",
        "sections": ["control_bus", "scene_bank", "mixer", "mapper", "safety"],
        "operators": [
            {"name": "osc_in", "type": "OSC In CHOP", "inputs": [None]},
            {"name": "control", "type": "Constant CHOP", "inputs": [None]},
            {"name": "scene_a", "type": "Movie File In TOP", "inputs": [None]},
            {"name": "scene_b", "type": "Movie File In TOP", "inputs": [None]},
            {"name": "switch", "type": "Switch TOP", "inputs": ["scene_a", "scene_b"]},
            {"name": "blackout", "type": "Level TOP", "inputs": ["switch"]},
            {"name": "out", "type": "Out TOP", "inputs": ["blackout"]},
        ],
    },
}


def list_scaffolds() -> List[str]:
    return sorted(SCAFFOLDS)


def scaffold_show(project_type: str = "vj") -> Dict[str, Any]:
    """Return a show scaffold blueprint for ``project_type``.

    Valid types: ``vj``, ``audio_reactive``, ``installation``. Returns a dict
    with the scaffold's sections + operators; callers can pass the
    ``operators`` list straight into ``td_build_network``.
    """
    if project_type not in SCAFFOLDS:
        return {"error": f"unknown scaffold {project_type!r}",
                "available": list_scaffolds()}
    scaffold = SCAFFOLDS[project_type]
    return {
        "name": project_type,
        "description": scaffold["description"],
        "sections": scaffold["sections"],
        "operators": scaffold["operators"],
    }
