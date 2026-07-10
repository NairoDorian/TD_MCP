"""Spatial pointer resolver (twozero `*here` / `*this`).

twozero's killer UX trick: an agent references the *currently active network*
with ``*here`` and the *currently selected operator* with ``*this op`` instead
of hard-coding a path. This pure resolver turns those tokens into concrete
paths given a small project context, so the live client can accept them
transparently.

Run:  uv run python -m tests.test_spatial
"""

from __future__ import annotations

from typing import Any, Dict

HERE = "*here"
THIS = "*this"
THIS_OP = "*this op"


def resolve_pointer(token: str, context: Dict[str, Any]) -> str:
    """Resolve a single pointer token against a context.

    ``context`` keys: ``pane_path`` (current network), ``selected`` (selected
    op path), ``here`` (alias for pane_path).
    """
    if token == HERE or token == "*":
        return context.get("pane_path") or context.get("here") or "/project1"
    if token in (THIS, THIS_OP):
        return context.get("selected") or context.get("pane_path") or context.get("here") or "/project1"
    return token


def resolve_args(args: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively replace ``*here`` / ``*this`` tokens in string values and
    in any ``path``-like keys of a tool-args dict."""
    out: Dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str) and value.strip() in (HERE, THIS, THIS_OP, "*"):
            out[key] = resolve_pointer(value.strip(), context)
        elif isinstance(value, dict):
            out[key] = resolve_args(value, context)
        elif isinstance(value, list):
            out[key] = [
                resolve_args(v, context) if isinstance(v, dict) else
                (resolve_pointer(v, context) if isinstance(v, str) and
                 v.strip() in (HERE, THIS, THIS_OP, "*") else v)
                for v in value
            ]
        else:
            out[key] = value
    return out
