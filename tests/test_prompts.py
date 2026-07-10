"""Tests for td_mcp.prompts (expert prompts / phases)."""

from td_mcp.prompts import build_phase_prompt, get_expert_prompt, list_experts


def test_experts_present():
    for n in ("td_designer", "network_builder", "td_glsl_expert",
              "td_python_expert", "ui_expert", "critic"):
        assert n in list_experts()
        assert get_expert_prompt(n)


def test_phase_prompt_combines():
    p = build_phase_prompt("build")
    assert "phase: build" in p
    assert "network_builder" in p


def test_unknown_expert_returns_none():
    assert get_expert_prompt("ghost") is None
