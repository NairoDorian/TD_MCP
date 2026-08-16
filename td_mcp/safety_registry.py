"""Source registry + show-readiness checklist (ai-brain idea).

A vetted list of TouchDesigner asset/MCP/reference sources with license and
TD-version tracking, plus the canonical live-build/show-readiness checklist.
These are data-first modules — pure stdlib, no MCP/network dependency.
"""

from __future__ import annotations

from typing import Any, Dict, List

# --- Source registry (ai-brain registry/sources.yml shape) -------------------
SOURCES: List[Dict[str, str]] = [
    {
        "id": "td-docs-wiki",
        "name": "TouchDesigner Official Wiki",
        "type": "first-party-docs",
        "url": "https://docs.derivative.ca",
        "priority": "primary",
        "notes": "Official operator/Python/GLSL reference; MIT-free — treat as authoritative.",
    },
    {
        "id": "bottobot-td-mcp",
        "name": "bottobot/td-mcp",
        "type": "mcp-reference",
        "url": "https://github.com/bottobot/touchdesigner-mcp-server",
        "priority": "reference",
        "notes": "MIT. Version-aware JSON corpus (661 operators, 214 Python classes) merged into TD_MCP.",
    },
    {
        "id": "tdmcp",
        "name": "Pantani/tdmcp",
        "type": "mcp-reference",
        "url": "https://github.com/Pantani/tdmcp",
        "priority": "reference",
        "notes": "MIT. 355-tool surface, embedded KB, recipe library; ideas reimplemented in TD_MCP.",
    },
    {
        "id": "touchdesigner-wiki-rag",
        "name": "touchdesigner-wiki-rag",
        "type": "mcp-reference",
        "url": "https://github.com/johnnyvincentvitale/touchdesigner-wiki-rag",
        "priority": "reference",
        "notes": "MIT. Wiki-wide SQLite FTS5 + Chroma; consumed via TD_MCP_WIKI_DB.",
    },
    {
        "id": "embody",
        "name": "Embody",
        "type": "mcp-live-build",
        "url": "https://github.com/dylanroscover/Embody",
        "priority": "reference",
        "notes": "MIT. TDN YAML, ui.undo, recovery hints; techniques reimplemented in TD_MCP.",
    },
    {
        "id": "td-builder-alpha",
        "name": "TrueFiasco/TD_Builder_alpha",
        "type": "mcp-reference",
        "url": "https://github.com/TrueFiasco/TD_Builder_alpha",
        "priority": "reference",
        "notes": "AGPL-3.0. Techniques only (hybrid RAG, eval gate, param families); no code copied.",
    },
]

# --- Show-readiness checklist (ai-brain checklists/live-build-checklist) -----
SHOW_CHECKLIST: Dict[str, List[str]] = {
    "before_automation": [
        "Start from a copy of the .toe file",
        "Confirm target TD version and license type",
        "Confirm timeline is running",
        "Confirm project folder is a git repo if using Embody auto .mcp.json",
        "Confirm MCP bridge is bound to localhost only",
        "Confirm no secrets stored in parameters, DATs, or local config files",
    ],
    "before_show": [
        "Test at target resolution and refresh rate",
        "Verify audio input routing and fallback",
        "Verify controller/MIDI/OSC routing and fallback",
        "Verify blackout, freeze, bypass, and reset",
        "Verify mapping calibration is saved and restorable",
        "Verify logs/performance telemetry are visible outside the projection output",
        "Record known-good FPS and GPU/CPU cook budgets",
    ],
    "asset_intake": [
        "Source URL",
        "Author",
        "License",
        "Downloaded date",
        "TD version saved with",
        "Whether it uses Python/network/external files/compiled plugins",
        "Smoke-test result",
        "Visual screenshot or output capture",
        "Decision: keep | sandbox | rewrite | reject",
    ],
}


def list_sources() -> List[Dict[str, str]]:
    return SOURCES


def source_by_id(source_id: str) -> Dict[str, Any]:
    for s in SOURCES:
        if s["id"] == source_id:
            return dict(s)
    return {"error": f"unknown source {source_id!r}", "available": [s["id"] for s in SOURCES]}


def show_readiness(section: str = "all") -> Dict[str, Any]:
    """Return the show-readiness checklist, all sections or one."""
    if section == "all":
        return {"checklist": SHOW_CHECKLIST}
    if section not in SHOW_CHECKLIST:
        return {"error": f"unknown section {section!r}",
                "available": list(SHOW_CHECKLIST)}
    return {"section": section, "items": SHOW_CHECKLIST[section]}
