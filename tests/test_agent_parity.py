"""Agent↔bridge tool-surface parity.

The in-TD agent (``bridge/td_mcp_agent.py``) carries its own ``TOOLS_SCHEMA``
that the running LLM sees.  When a tool exists in the bridge ``_DISPATCH`` but
is missing from the agent schema the model simply cannot call it — it silently
loses a capability.  Conversely, a schema entry with no handler (or a handler
branch whose name does not match the schema name) means the model sees a tool
that immediately fails.

These tests pin the three invariants we learned the hard way:

  1. Every bridge tool is visible to the agent.  ``batch``,
     ``set_dat_text``, ``observe``, etc. must survive in TOOLS_SCHEMA.
  2. Every *client-side* tool the agent declares (vision, macros, sessions,
     clarification, …) has a real ``elif name == "X":`` dispatch branch so a
     call never falls through to the bridge as "unknown tool".
  3. The schema name and the dispatch name for each client-side tool are the
     same string (regression guard for the ask_clarification/ask_user drift).
"""
from __future__ import annotations

import os
import re

import pytest

from bridge.td_mcp_agent import TOOLS_SCHEMA

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BRIDGE_PATH = os.path.join(ROOT, "bridge", "td_mcp_bridge.py")
AGENT_PATH = os.path.join(ROOT, "bridge", "td_mcp_agent.py")

#: Tools handled entirely client-side; they are NOT bridge tools and need a
#: local dispatch branch rather than falling through to _execute_with_recovery.
CLIENT_ONLY = {
    "caption_viewport",
    "macro_start", "macro_stop", "macro_save", "macro_load",
    "macro_replay", "macro_status",
    "plan_task", "ask_clarification", "save_session", "load_session",
    "execute_parallel",
}


def _bridge_dispatch_keys() -> set[str]:
    text = open(BRIDGE_PATH, encoding="utf-8").read()
    start = text.index("_DISPATCH = {") + len("_DISPATCH = {")
    end = text.index("\n}\n", start)
    block = text[start:end]
    return set(re.findall(r'^\s*"([a-z0-9_]+)":\s*lambda', block, re.M))


def _agent_schema_names() -> set[str]:
    return {t["function"]["name"] for t in TOOLS_SCHEMA}


def _agent_dispatch_branches() -> set[str]:
    """Names handled by an ``if``/``elif name == "X":`` branch in ``chat()``."""
    text = open(AGENT_PATH, encoding="utf-8").read()
    return set(re.findall(r'\b(?:if|elif)\s+name\s*==\s*"([a-z0-9_]+)"', text))


# ---------------------------------------------------------------------------
# Invariant 1: every bridge tool is visible to the agent
# ---------------------------------------------------------------------------

def test_agent_schema_covers_all_bridge_tools():
    missing = _bridge_dispatch_keys() - _agent_schema_names()
    assert not missing, f"bridge tools missing from agent TOOLS_SCHEMA: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Invariant 2: no orphan agent tools (non-client-only must exist on the bridge)
# ---------------------------------------------------------------------------

def test_agent_schema_has_no_orphan_bridge_tools():
    schema = _agent_schema_names()
    bridge = _bridge_dispatch_keys()
    orphan = (schema - bridge) - CLIENT_ONLY
    assert not orphan, f"agent tools not in bridge and not client-only: {sorted(orphan)}"


# ---------------------------------------------------------------------------
# Invariant 3: every client-side tool declared in the schema has a dispatch
#              branch, AND the dispatch name matches the schema name exactly.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool", sorted(CLIENT_ONLY))
def test_client_side_tool_has_matching_dispatch(tool):
    schema_names = _agent_schema_names()
    branches = _agent_dispatch_branches()
    assert tool in schema_names, f"{tool} missing from TOOLS_SCHEMA"
    assert tool in branches, f"{tool}: declared in schema but no dispatch branch"


def test_no_stale_dispatch_names():
    """A dispatch branch for a name absent from the schema is dead code."""
    branches = _agent_dispatch_branches()
    schema = _agent_schema_names()
    # client-only tools are the only ones dispatched locally
    stale = branches - schema
    assert not stale, f"dispatch branches with no schema entry: {sorted(stale)}"


if __name__ == "__main__":
    test_agent_schema_covers_all_bridge_tools()
    test_agent_schema_has_no_orphan_bridge_tools()
    for t in sorted(CLIENT_ONLY):
        test_client_side_tool_has_matching_dispatch(t)
    print("AGENT-PARITY TESTS PASSED")
