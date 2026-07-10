"""Per-client MCP config + skill generation (Embody / twozero).

Both Embody and twozero auto-generate the exact ``.mcp.json`` (or
``claude_desktop_config.json``) an AI client needs, plus a tailored
``CLAUDE.md`` / ``AGENTS.md`` describing the tools — so a human gets a
one-step install instead of hand-editing JSON. This module produces those
artifacts for td-mcp's two servers and a skill file, preserving nothing
external (pure generation, no file hashing needed for our use).

Run:  uv run python -m tests.test_config_gen
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

PROJECT = "C:/Users/Z/Downloads/PROJECTS/TOUCHDESIGNER/td-mcp"


def generate_mcp_json(project_dir: str = PROJECT, include_offline: bool = True,
                       include_live: bool = True,
                       auth_token: Optional[str] = None) -> Dict[str, Any]:
    """Build the mcpServers block for an HTTP-capable or stdio client."""
    servers: Dict[str, Any] = {}
    if include_offline:
        servers["td-mcp-offline"] = {
            "command": "uv",
            "args": ["run", "--project", project_dir, "td-mcp-offline", "--mcp"],
        }
    if include_live:
        live: Dict[str, Any] = {
            "command": "uv",
            "args": ["run", "--project", project_dir, "td-mcp-live", "--mcp"],
        }
        if auth_token:
            live["env"] = {"TD_MCP_AUTH_TOKEN": auth_token}
        servers["td-mcp-live"] = live
    return {"mcpServers": servers}


def generate_client_docs(client: str = "claude",
                         tools: Optional[List[str]] = None) -> str:
    """Generate a short AGENTS/CLAUDE skill doc for the chosen client."""
    tools = tools or ["td_docs_search", "td_docs_operator", "td_docs_parameter",
                      "td_build_network", "create_node", "set_parameters",
                      "connect_nodes", "build_and_verify", "scan_network",
                      "capture_viewport"]
    lines = [
        f"# td-mcp for {client}",
        "",
        "Local-first TouchDesigner MCP: an offline doc/RAG server plus a live",
        "bridge client. Query docs offline; control a running TD over the bridge.",
        "",
        "## Tools",
    ]
    for t in tools:
        lines.append(f"- `{t}`")
    lines += [
        "",
        "## Rules",
        "- Prefer `td_docs_*` for facts before guessing parameter names.",
        "- Use `build_and_verify` for create->verify->preview; it returns a",
        "  recovery hint on failure so you can self-correct.",
        "- Wrap batches in `batch` and rely on the bridge's undo (Ctrl+Z).",
        "- Respect risk tiers: `delete_node`/`execute_python` are DESTRUCTIVE.",
    ]
    return "\n".join(lines)


def generate_skill(client: str = "claude") -> Dict[str, str]:
    """Return a bundle of generated config artifacts (string contents)."""
    return {
        "mcp.json": json.dumps(generate_mcp_json(), indent=2),
        "CLAUDE.md": generate_client_docs(client),
    }


def write_configs(target_dir: str, project_dir: str = PROJECT,
                  client: str = "claude", auth_token: Optional[str] = None) -> Dict[str, str]:
    """Write the generated artifacts to ``target_dir``. Returns the file paths."""
    import os
    os.makedirs(target_dir, exist_ok=True)
    bundle = generate_skill(client)
    paths: Dict[str, str] = {}
    mp = os.path.join(target_dir, ".mcp.json")
    with open(mp, "w", encoding="utf-8") as f:
        f.write(bundle["mcp.json"])
    paths["mcp"] = mp
    cd = os.path.join(target_dir, "CLAUDE.md")
    with open(cd, "w", encoding="utf-8") as f:
        f.write(bundle["CLAUDE.md"])
    paths["docs"] = cd
    # Also a claude_desktop_config.json flavor for stdio clients.
    if client == "claude-desktop":
        cfg = os.path.join(target_dir, "claude_desktop_config.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump(generate_mcp_json(project_dir, auth_token=auth_token), f, indent=2)
        paths["desktop_config"] = cfg
    return paths
