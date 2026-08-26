"""MCP Bundle (``.mcpb``) packaging.

A ``.mcpb`` is a zip an MCP host installs in one double-click. The host reads
**``manifest.json`` at the archive root** and nothing else — there is no
discovery fallback, no alternative filename.

Wave 12 fixed this module: it previously wrote a member called ``server.json``
carrying a schema of its own invention (``{"name", "version", "description",
"command", "args", "format": "mcpb"}``). Every field was plausible and none of
them was the contract, so the produced bundle was a correctly-named zip that no
client could install. Nothing failed loudly, because nothing had ever tried to
install one; the roadmap item read as nearly done.

The real shape, per the MCPB spec (and confirmed against a shipping bundle,
``github-mcp/touchdesigner-mcp-superdwayne/manifest.json``)::

    {
      "manifest_version": "0.2",
      "name": "td-mcp", "version": "1.11.0", "description": "...",
      "author": {"name": "..."},
      "server": {
        "type": "python",
        "entry_point": "td_mcp/server_offline.py",
        "mcp_config": {"command": "python", "args": [...], "env": {...}}
      }
    }

``${__dirname}`` is expanded by the host to the unpacked bundle directory, so
every path inside ``mcp_config`` must go through it — an absolute path baked at
build time points at the *build* machine.

Run:  uv run python -m tests.test_bundle
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

#: The spec version this module emits.
MANIFEST_VERSION = "0.2"

#: The manifest member name the host looks for. Not configurable: a bundle with
#: the file anywhere else is simply not a bundle.
MANIFEST_NAME = "manifest.json"

#: Server types the spec defines.
SERVER_TYPES = ("python", "node", "binary")

#: Token the host expands to the unpacked bundle directory.
DIRNAME = "${__dirname}"

_DEFAULT_EXCLUDES = (".venv", ".git", "__pycache__", ".pytest_cache",
                     "node_modules", ".mypy_cache", ".ruff_cache")


class ManifestError(ValueError):
    """A manifest that a host would reject at install time."""


def build_manifest(name: str,
                   command: str,
                   args: List[str],
                   version: str = "1.0.0",
                   description: str = "",
                   entry_point: str = "td_mcp/server_offline.py",
                   server_type: str = "python",
                   env: Optional[Dict[str, str]] = None,
                   author: Optional[Dict[str, str]] = None,
                   license_name: str = "MIT",
                   homepage: Optional[str] = None,
                   keywords: Optional[List[str]] = None,
                   user_config: Optional[Dict[str, Dict]] = None) -> Dict:
    """Build a spec-shaped manifest.

    The signature keeps ``(name, command, args)`` first so existing callers
    keep working; everything the spec additionally requires has a default.
    """
    if server_type not in SERVER_TYPES:
        raise ManifestError(
            f"server.type must be one of {SERVER_TYPES}, got {server_type!r}")
    manifest = {
        # `manifest_version` is the current key; `dxt_version` is the name it
        # had before the format was renamed. Hosts in the field still read
        # either, and emitting both costs one line and installs everywhere.
        "manifest_version": MANIFEST_VERSION,
        "dxt_version": MANIFEST_VERSION,
        "name": name,
        "version": version,
        "description": description,
        "author": author or {"name": "NairoDorian",
                             "url": "https://github.com/NairoDorian/TD_MCP"},
        "license": license_name,
        "server": {
            "type": server_type,
            "entry_point": entry_point,
            "mcp_config": {
                "command": command,
                "args": list(args),
                "env": dict(env or {}),
            },
        },
    }
    if homepage:
        manifest["homepage"] = homepage
    if keywords:
        manifest["keywords"] = list(keywords)
    if user_config:
        manifest["user_config"] = user_config
    return manifest


def td_mcp_manifest(version: str, live: bool = False) -> Dict:
    """The manifest this project actually ships.

    ``PYTHONPATH`` is anchored to ``${__dirname}`` so the unpacked bundle is
    importable wherever the host put it; a path resolved at build time would
    point at the machine that built the bundle.
    """
    module = "td_mcp.server_live" if live else "td_mcp.server_offline"
    entry = f"td_mcp/{'server_live' if live else 'server_offline'}.py"
    user_config = {
        "bridge_host": {
            "type": "string", "title": "TouchDesigner bridge host",
            "default": "127.0.0.1", "required": False,
        },
        "bridge_port": {
            "type": "number", "title": "TouchDesigner bridge port",
            "default": 9980, "required": False,
        },
    } if live else None
    return build_manifest(
        name="td-mcp-live" if live else "td-mcp",
        command="python",
        args=["-m", module, "--mcp"],
        version=version,
        description=("TouchDesigner live bridge: create operators, wire "
                     "networks, read all four diagnostic surfaces."
                     if live else
                     "TouchDesigner documentation, RAG and offline network "
                     "authoring. No running TouchDesigner required."),
        entry_point=entry,
        server_type="python",
        env={"PYTHONPATH": DIRNAME, "TD_MCP_MODE": "mcp"},
        homepage="https://github.com/NairoDorian/TD_MCP",
        keywords=["touchdesigner", "mcp", "rag", "creative-coding",
                  "visual-programming"],
        user_config=user_config,
    )


def validate_manifest(manifest: Dict) -> List[str]:
    """Problems a host would trip over, as a list. Empty means installable."""
    problems: List[str] = []
    if not isinstance(manifest, dict):
        return ["manifest is not an object"]
    if not (manifest.get("manifest_version") or manifest.get("dxt_version")):
        problems.append("missing manifest_version")
    for field in ("name", "version"):
        if not manifest.get(field):
            problems.append(f"missing {field}")
    server = manifest.get("server")
    if not isinstance(server, dict):
        problems.append("missing server block")
        return problems
    if server.get("type") not in SERVER_TYPES:
        problems.append(f"server.type must be one of {SERVER_TYPES}")
    if not server.get("entry_point"):
        problems.append("missing server.entry_point")
    cfg = server.get("mcp_config")
    if not isinstance(cfg, dict):
        problems.append("missing server.mcp_config")
        return problems
    if not cfg.get("command"):
        problems.append("missing server.mcp_config.command")
    if not isinstance(cfg.get("args"), list):
        problems.append("server.mcp_config.args must be a list")
    # A path fixed at build time points at the build machine, not the install.
    for value in list(cfg.get("args") or []) + list((cfg.get("env") or {}).values()):
        text = str(value)
        if os.path.isabs(text) and DIRNAME not in text:
            problems.append(f"absolute path baked into mcp_config: {text!r} "
                            f"(use {DIRNAME})")
    return problems


def package(project_dir: str, out_path: str, files: Optional[List[str]] = None,
            manifest: Optional[Dict] = None, exclude: Optional[List[str]] = None
            ) -> str:
    """Create a ``.mcpb`` bundle. Raises ``ManifestError`` if it would not install.

    ``files`` = explicit relative file list; if omitted, bundles everything
    under ``project_dir`` except ``exclude`` dirs.
    """
    project_path = Path(project_dir)
    exclude_set = set(exclude or _DEFAULT_EXCLUDES)
    if manifest is None:
        manifest = build_manifest(
            project_path.name, "python",
            ["-m", f"{project_path.name.replace('-', '_')}.server_offline",
             "--mcp"],
            env={"PYTHONPATH": DIRNAME})

    problems = validate_manifest(manifest)
    if problems:
        # Refusing here is the whole point: a bundle that fails validation is
        # indistinguishable from a working one until someone tries to install
        # it, which is how the old server.json shipped unnoticed.
        raise ManifestError("manifest would not install: " + "; ".join(problems))

    out_path = str(out_path)
    if not out_path.endswith(".mcpb"):
        out_path += ".mcpb"

    if files is None:
        files = []
        for root, dirs, fnames in os.walk(project_path):
            dirs[:] = [d for d in dirs if d not in exclude_set]
            for fn in fnames:
                files.append(os.path.relpath(os.path.join(root, fn), project_path))

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
        for rel in files:
            # Reject entries that escape project_dir (classic zip-slip), and
            # never let a stray manifest.json in the tree shadow ours.
            if os.path.isabs(rel) or any(part == ".." for part in
                                         rel.split("/") + rel.split("\\")):
                continue
            if rel.replace("\\", "/") == MANIFEST_NAME:
                continue
            fp = project_path / rel
            if fp.is_file():
                z.write(fp, rel)
    return out_path


def read_manifest(bundle_path: str) -> Dict:
    """Read a bundle's manifest the way a host would."""
    with zipfile.ZipFile(bundle_path) as z:
        names = z.namelist()
        if MANIFEST_NAME not in names:
            raise ManifestError(
                f"{bundle_path} has no {MANIFEST_NAME} at the archive root; "
                f"an MCP host would reject it")
        return json.loads(z.read(MANIFEST_NAME).decode("utf-8"))
