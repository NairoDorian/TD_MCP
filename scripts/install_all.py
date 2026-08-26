#!/usr/bin/env python3
"""One-command install (ai-brain / johnsabath / 8beeeaaat idea).

Installs the package, builds the KB, writes every client config, and prints
the exact registration JSON for Claude Desktop, Cursor, Codex, Copilot, and
Gemini.  Pure stdlib; safe to re-run (idempotent).

Run:
    uv run python scripts/install_all.py
    uv run python scripts/install_all.py --skip-build-kb
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TD_MCP = ROOT / "td_mcp"


def _run(cmd, cwd=None):
    print(f"  $ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=cwd or str(ROOT), check=False)


def build_kb():
    print("Building knowledge base...", flush=True)
    _run(["uv", "run", "python", "-m", "td_mcp.kb.build_kb"])


def install_package(extra="test"):
    print(f"Installing td-mcp .[{extra}]...", flush=True)
    _run(["uv", "pip", "install", "-e", f".[{extra}]"])


def write_client_configs():
    """Write client configs + skill files into a configs/ directory."""
    import td_mcp.config_gen as cg
    print("Writing client configs...", flush=True)
    out_dir = ROOT / "client_configs"
    out_dir.mkdir(exist_ok=True)
    cg.write_configs(str(out_dir), project_dir=str(ROOT))
    for f in sorted(out_dir.iterdir()):
        print(f"  {f.name}")
    skill = cg.generate_skill("claude")
    if skill:
        skill_dir = ROOT / "skills" / "td-building"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            skill.get("skill", ""), encoding="utf-8")
        print("  skills/td-building/SKILL.md")


def print_registration_json():
    repo = str(ROOT).replace("\\", "/")
    config = {
        "mcpServers": {
            "td-mcp-offline": {
                "command": "uv",
                "args": ["run", "--project", repo, "td-mcp-offline", "--mcp"],
            },
            "td-mcp-live": {
                "command": "uv",
                "args": ["run", "--project", repo, "td-mcp-live", "--mcp"],
                "env": {"TD_MCP_AUTH_TOKEN": "YOUR_AUTO_GENERATED_TOKEN"},
            },
        }
    }
    print("\nRegistration JSON (copy into claude_desktop_config.json / mcp.json):")
    print(json.dumps(config, indent=2))


def main():
    ap = argparse.ArgumentParser(description="One-command td-mcp install.")
    ap.add_argument("--skip-build-kb", action="store_true",
                    help="skip the chunks.jsonl build step")
    ap.add_argument("--json", action="store_true",
                    help="emit the Install SOP report instead of prose")
    args = ap.parse_args()

    install_package()
    if not args.skip_build_kb:
        build_kb()
    write_client_configs()
    print_registration_json()

    # Hand off to the Install SOP for the part that has to be honest about
    # what a CLI cannot do: stage the bridge, write a receipt, and name the
    # step that must happen inside TouchDesigner. `install_all` used to end
    # with "Done. td-mcp-offline + td-mcp-live are ready." -- true of the
    # servers, and quietly untrue of the bridge, which no external process can
    # install into a .toe.
    from td_mcp import install as sop

    argv = ["install", "--yes"] + (["--json"] if args.json else [])
    code = sop.main(argv)
    if code == sop.EXIT_REQUIRES_RESTART:
        print("\nServers are ready. The bridge is staged and needs the "
              "TouchDesigner step above; then run `td-mcp-install verify`.")
        return 0
    return code


if __name__ == "__main__":
    raise SystemExit(main())
