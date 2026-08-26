"""Schema↔handler parity tests (TDPilot_deepseekv4 strongest regression guard).

Three surfaces must stay in lockstep:

1. **Live MCP handlers** — `server_live.TOOL_REGISTRY` (what `call_tool`
   dispatches) and `_TOOL_META` (what `tools/list` advertises).
2. **TD bridge dispatch** — `bridge/td_mcp_bridge.py::_DISPATCH` (what the
   in-TD WebServer DAT actually handles).
3. **Live HTTP metadata** — `_http_tool_meta()` (the Streamable-HTTP
   `tools/list` schema surface).

Any handler that is missing from an advertised schema means the LLM can see a
tool but not call it (or vice versa) — the exact drift TDPilot guards against.
"""

import os
import re

from td_mcp.server_live import TOOL_REGISTRY, _TOOL_META, _http_tool_meta


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE_PATH = os.path.join(HERE, "bridge", "td_mcp_bridge.py")


def _bridge_dispatch_keys():
    """Extract the keys of ``_DISPATCH`` from the bridge source (the bridge is
    TD-side code that cannot be imported by pytest)."""
    text = open(BRIDGE_PATH, encoding="utf-8").read()
    # Extract from `_DISPATCH = {` through the final `}` before the `_redact_paths`
    # helper.  The lambdas' bodies may contain dict literals, so splitting on the
    # first `}` truncates — use the closing brace at line start instead.
    start = text.index("_DISPATCH = {") + len("_DISPATCH = {")
    end = text.index("\n}\n", start)
    block = text[start:end]
    return set(re.findall(r'^\s*"([a-z0-9_]+)":\s*lambda', block, re.M))


def test_live_registry_matches_tool_meta():
    """Every live tool the server can call is advertised in tools/list."""
    assert set(TOOL_REGISTRY) == {name for name, *_ in _TOOL_META}, \
        set(TOOL_REGISTRY) ^ {name for name, *_ in _TOOL_META}


def test_live_registry_matches_http_meta():
    """The Streamable-HTTP tools/list surface matches the registry exactly."""
    meta_names = {t["name"] for t in _http_tool_meta()}
    assert set(TOOL_REGISTRY) == meta_names, set(TOOL_REGISTRY) ^ meta_names


def test_bridge_dispatch_matches_live_registry():
    """The in-TD bridge dispatch and the live client registry stay in lockstep.

    ``TDClient``'s bridge methods mirror the registry; the real parity contract
    is between what the live MCP server advertises and what the bridge can
    actually execute. Both are derived from the same tool list, so a drift here
    is a broken tool the agent can see but not run.
    """
    bridge_keys = _bridge_dispatch_keys()
    # `get_non_default_params` is a client-side convenience: it calls get_node
    # then filters, so it does not need a separate bridge handler.
    client_only = {"get_non_default_params"}
    assert bridge_keys == (set(TOOL_REGISTRY) - client_only), \
        bridge_keys ^ (set(TOOL_REGISTRY) - client_only)


def test_bridge_dispatch_has_new_safety_tool():
    """The Phase 3 ``set_dat_text`` CAS tool is wired in both surfaces."""
    bridge_keys = _bridge_dispatch_keys()
    assert "set_dat_text" in bridge_keys
    assert "set_dat_text" in TOOL_REGISTRY
    assert "set_dat_text" in {name for name, *_ in _TOOL_META}


def test_tool_meta_schemas_have_real_types():
    """Every advertised tool has an inputSchema with typed properties."""
    for name, desc, props in _TOOL_META:
        assert isinstance(props, dict), name
        for pname, pspec in props.items():
            assert isinstance(pspec, dict), (name, pname)
            assert pspec.get("type") in ("string", "integer", "number", "object", "array", "boolean"), \
                (name, pname, pspec)


def test_every_live_tool_has_an_explicit_risk_class():
    """A new tool must arrive with a reviewed classification, never a default.

    ``risk_class`` falls back to WRITE_ADDITIVE so an unclassified tool can
    never read as read-only, but the fallback is a safety net, not a policy: a
    capability that reaches the model without anyone deciding what it may do is
    exactly the drift Embody's host-app guard refuses (v6.0.264).
    """
    from td_mcp.tools.risk import RISK_CLASS

    missing = sorted(set(TOOL_REGISTRY) - set(RISK_CLASS))
    assert not missing, f"tools with no explicit risk class: {missing}"


def test_every_offline_tool_has_an_explicit_risk_class():
    """Offline tools are pure functions over a description and must say so.

    They were relying on the WRITE_ADDITIVE fallback, so a client running a
    read-only policy refused plain documentation lookups.
    """
    from td_mcp.server_offline import _OFFLINE_DISPATCH
    from td_mcp.tools.risk import RISK_CLASS

    missing = sorted(set(_OFFLINE_DISPATCH) - set(RISK_CLASS))
    assert not missing, f"offline tools with no explicit risk class: {missing}"


def test_doc_tools_are_read_only():
    """A documentation lookup must survive TD_BUILDER_LIVE_READONLY=1."""
    from td_mcp.server_offline import _OFFLINE_DISPATCH
    from td_mcp.tools.risk import risk_class

    for name in _OFFLINE_DISPATCH:
        if name.startswith("td_docs_"):
            assert risk_class(name) == "READ_ONLY", name


def test_code_bearing_tools_are_classified_destructive():
    """Everything that hands TouchDesigner caller-supplied Python is DESTRUCTIVE.

    A parameter expression is evaluated; a DAT's text *is* the program for an
    Execute/Script DAT. Classifying either below `execute_python` would let a
    max-risk policy admit code it meant to block.
    """
    from td_mcp.tools.risk import risk_class

    for tool in ("execute_python", "bind_parameter_expression", "set_dat_text"):
        assert risk_class(tool) == "DESTRUCTIVE", tool
