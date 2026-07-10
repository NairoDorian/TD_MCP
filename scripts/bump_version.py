#!/usr/bin/env python3
"""Bump the td-mcp version in ONE place and keep the changelog in sync.

td-mcp's version has exactly two sources of truth:

  * ``pyproject.toml``  -> ``version = "x.y.z"``   (the canonical version)
  * ``CHANGELOG.md``    -> top heading ``## [x.y.z] - YYYY-MM-DD``  (must match)

Runtime code reads the version from the installed package metadata via
``td_mcp.__version__`` (which itself comes from ``pyproject.toml``), so no
``.py`` file needs editing when you bump.

This script updates both the ``pyproject.toml`` version and the *top* changelog
heading, and warns if any other file still references the old version.

Usage:
    uv run python scripts/bump_version.py 1.7.4
    python scripts/bump_version.py 2.0.0
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
CHANGELOG = ROOT / "CHANGELOG.md"

_VERSION_RE = re.compile(r'^\s*version\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_CHANGELOG_HEAD_RE = re.compile(r'^##\s*\[([^\]]+)\]', re.MULTILINE)


def current_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    m = _VERSION_RE.search(text)
    if not m:
        raise SystemExit("could not find version= in pyproject.toml")
    return m.group(1)


def bump_pyproject(new: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    text = _VERSION_RE.sub(f'version = "{new}"', text, count=1)
    PYPROJECT.write_text(text, encoding="utf-8", newline="\n")


def bump_changelog(new: str) -> None:
    if not CHANGELOG.exists():
        return
    text = CHANGELOG.read_text(encoding="utf-8")
    today = date.today().isoformat()
    # Replace the ENTIRE top heading line (version + date), not just the [version] token.
    def _replace(match: re.Match) -> str:
        return f"## [{new}] - {today}"

    new_text = re.sub(r'^##\s*\[[^\]]+\].*$', _replace, text, count=1, flags=re.MULTILINE)
    CHANGELOG.write_text(new_text, encoding="utf-8", newline="\n")


def scan_stale(old: str) -> list[str]:
    """Find files still hardcoding the old version (excluding build/changelog)."""
    stale: list[str] = []
    skip_dirs = {".git", ".venv", "__pycache__", ".pytest_cache", ".egg-info"}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            if fn in ("CHANGELOG.md", "pyproject.toml"):
                continue
            if fn.endswith((".pyc",)):
                continue
            p = Path(dirpath) / fn
            try:
                body = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            # Match the exact old version as a standalone token.
            if re.search(r'(?<![\d.])' + re.escape(old) + r'(?![\d.])', body):
                stale.append(str(p.relative_to(ROOT)))
    return stale


def main() -> int:
    ap = argparse.ArgumentParser(description="Bump the td-mcp version (pyproject + changelog).")
    ap.add_argument("new_version", help="new semantic version, e.g. 1.7.4")
    args = ap.parse_args()

    new = args.new_version
    if not re.match(r"^\d+\.\d+\.\d+", new):
        print(f"[bump_version] '{new}' does not look like x.y.z", file=sys.stderr)
        return 2

    old = current_version()
    if old == new:
        print(f"[bump_version] already at {new}; nothing to do.")
        return 0

    bump_pyproject(new)
    bump_changelog(new)
    print(f"[bump_version] {old} -> {new}")
    print(f"[bump_version] updated pyproject.toml and the top CHANGELOG.md heading.")

    stale = scan_stale(old)
    if stale:
        print("\n[bump_version] WARNING — these files still mention the old version "
              f"({old}); review them:")
        for s in stale:
            print(f"  - {s}")
    else:
        print("[bump_version] no other files hardcode the old version. All good.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
