"""Project bundling (.mcpb) (tdmcp multi-client packaging).

tdmcp ships a ``.mcpb`` bundle so a server + skills can be dropped into
Claude Code / Codex / Cursor in one file. This module builds that archive:
a zip containing a ``server.json`` manifest plus the bundled files. Pure
zip I/O, unit-testable.

Run:  uv run python -m tests.test_bundle
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from typing import Dict, List, Optional


def build_manifest(name: str, command: str, args: List[str],
                   version: str = "1.0.0", description: str = "") -> Dict:
    return {
        "name": name,
        "version": version,
        "description": description,
        "command": command,
        "args": args,
        "format": "mcpb",
    }


def package(project_dir: str, out_path: str, files: Optional[List[str]] = None,
            manifest: Optional[Dict] = None, exclude: Optional[List[str]] = None
            ) -> str:
    """Create a ``.mcpb`` (zip) bundle.

    ``files`` = explicit relative file list; if omitted, bundles everything
    under ``project_dir`` except ``exclude`` dirs (default: .venv, .git,
    __pycache__, .pytest_cache).
    """
    project_dir = Path(project_dir)
    exclude = set(exclude or [".venv", ".git", "__pycache__", ".pytest_cache",
                              "node_modules"])
    manifest = manifest or build_manifest(
        project_dir.name, "uv",
        ["run", "--project", str(project_dir), f"{project_dir.name}-offline", "--mcp"])

    if files is None:
        files = []
        for root, dirs, fnames in os.walk(project_dir):
            dirs[:] = [d for d in dirs if d not in exclude]
            for fn in fnames:
                rel = os.path.relpath(os.path.join(root, fn), project_dir)
                files.append(rel)

    out_path = str(out_path)
    if not out_path.endswith(".mcpb"):
        out_path += ".mcpb"
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("server.json", json.dumps(manifest, indent=2))
        for rel in files:
            # Reject entries that escape project_dir (classic zip-slip).
            if os.path.isabs(rel) or any(part == ".." for part in rel.split("/") + rel.split("\\")):
                continue
            fp = project_dir / rel
            if fp.is_file():
                z.write(fp, rel)
    return out_path


def read_manifest(bundle_path: str) -> Dict:
    with zipfile.ZipFile(bundle_path) as z:
        return json.loads(z.read("server.json").decode("utf-8"))
