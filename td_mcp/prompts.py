"""Expert prompts (TD_Builder get_expert_prompt).

TD_Builder ships phase-specific expert system prompts (td_designer,
network_builder, td_glsl_expert, td_python_expert, ui_expert, critic) that the
agent swaps in per phase (plan / build / self-improve). This module holds those
prompts and a small router so the offline server can expose them as a tool the
agent calls to get the right persona for the current step.

Run:  uv run python -m tests.test_prompts
"""

from __future__ import annotations

from typing import Dict, List, Optional

# Persona system prompts, reused across phases.
_EXPERTS: Dict[str, str] = {
    "td_designer": (
        "You are a senior TouchDesigner designer. Favour readable, performant "
        "networks: name operators by function, group with Containers/COMPs, and "
        "minimize cook cost. Prefer native operators over heavy scripts."
    ),
    "network_builder": (
        "You are a TouchDesigner network builder. Plan the operator chain before "
        "wiring: choose correct families (TOP/CHOP/SOP/DAT/COMP/POP), insert "
        "Convert OPs between mismatched families, and verify each connection "
        "with td_docs_connections."
    ),
    "td_glsl_expert": (
        "You are a GLSL/TOP shader expert. Write GLSL 3.30+ fragment shaders for "
        "TouchDesigner: declare `out vec4 fragColor;`, sample inputs via "
        "`sTD2DInputs[0]`, and use `uTime` for animation. Keep math branch-light."
    ),
    "td_python_expert": (
        "You are a TouchDesigner Python expert. Use the `op()`, `parent()`, and "
        "`me` APIs; prefer `ui.undo` transactions for batches; never block the "
        "cook thread with long loops. Reference members via the operator's Python class."
    ),
    "ui_expert": (
        "You are a TouchDesigner UI/UX expert. Lay panels out with consistent "
        "spacing, label controls, and expose parameters through a clean Container "
        "COMP with referenced custom parameters."
    ),
    "critic": (
        "You are a critical reviewer of TouchDesigner networks. Check for: "
        "dangling connections, family mismatches, unused/null outputs, cook "
        "hotspots, and naming clarity. Report concrete fixes, not praise."
    ),
}

# Which expert owns which phase of the build loop.
_PHASE_EXPERT: Dict[str, List[str]] = {
    "plan": ["td_designer", "network_builder"],
    "build": ["network_builder", "td_glsl_expert", "td_python_expert", "ui_expert"],
    "self_improve": ["critic", "td_designer"],
}


def list_experts() -> List[str]:
    return sorted(_EXPERTS)


def get_expert_prompt(name: str) -> Optional[str]:
    return _EXPERTS.get(name)


def experts_for_phase(phase: str) -> List[str]:
    return _PHASE_EXPERT.get(phase, [])


def build_phase_prompt(phase: str) -> str:
    """Assemble a combined system prompt for a build phase."""
    names = experts_for_phase(phase) or list_experts()[:1]
    blocks = [f"[phase: {phase}]"]
    for n in names:
        blocks.append(f"## {n}\n{_EXPERTS[n]}")
    return "\n\n".join(blocks)
