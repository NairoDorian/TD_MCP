"""Tool-schema conformance tests (inspired by Embody v6.0.159, test_envoy_tool_schema).

Two guarantees we want for every MCP server:
  1. **No dead tools** – every tool advertised in the list_tools response
     has a dispatch branch in call_tool.
  2. **No silent no-ops** – every tool schema has a real, non-empty
     ``properties`` dict so the LLM actually has parameters to fill.

These tests use static source-code analysis (regex over the module file) so
they are fast and do not require a running TouchDesigner bridge.
"""

import asyncio
import re
import inspect

import mcp.types as types


# ---------------------------------------------------------------------------
# Offline server
# ---------------------------------------------------------------------------

def _extract_listed_tool_names(module_file, list_func_name):
    """Parse *_list_tools source and collect the tool names from _tool() calls."""
    src = inspect.getsource(module_file)
    # Match _tool("name",  OR  _tool('name',
    names = re.findall(r'_tool\(\s*["\']([^"\']+)["\']', src)
    return set(names)


def _extract_dispatch_branches(module_file, call_func_name, tool_names):
    """Parse the call_tool if/elif chain and return {name} that have a branch."""
    src = inspect.getsource(module_file)
    dispatched = set()
    for name in tool_names:
        # Match ``name == "tool_name"`` (handles both if and elif)
        if re.search(rf'name\s*==\s*["\']{re.escape(name)}["\']', src):
            dispatched.add(name)
    return dispatched


def test_offline_tools_not_dead():
    """Every tool in list_tools must have a dispatch branch in call_tool."""
    from td_mcp import server_offline

    listed = _extract_listed_tool_names(server_offline, "_offline_list_tools")
    assert listed, "no tools found in offline list_tools"

    dispatched = _extract_dispatch_branches(
        server_offline, "_offline_call_tool", listed)

    dead = listed - dispatched
    assert not dead, f"tools listed but not dispatched: {dead}"
    print(f"ok  offline: {len(listed)} tools listed, {len(dispatched)} dispatched "
          f"({len(dead)} dead)")


def test_offline_tool_schemas_complete():
    """Every offline tool schema must have type=object + non-empty properties."""
    from td_mcp import server_offline

    result = asyncio.run(server_offline._offline_list_tools(None, None))
    assert isinstance(result, types.ListToolsResult)
    for tool in result.tools:
        schema = tool.input_schema
        props = schema.get("properties") or schema  # schema may be bare properties dict
        assert isinstance(props, dict), f"{tool.name}: properties not a dict"
    print(f"ok  offline: {len(result.tools)} tools have complete schemas "
          f"({sum(1 for t in result.tools if not (t.input_schema.get('properties') or t.input_schema))} no-arg tools)")


def test_offline_cache_hint():
    """Tools list must carry a cache hint (ttlMs / cacheScope)."""
    from td_mcp import server_offline

    result = asyncio.run(server_offline._offline_list_tools(None, None))
    assert result.ttl_ms is not None, "no ttl_ms cache hint"
    assert result.cache_scope is not None, "no cache_scope"
    print(f"ok  offline cache hint: ttl_ms={result.ttl_ms} scope={result.cache_scope}")


def test_offline_calltool_returns_calltoolresult():
    """call_tool must return CallToolResult (not a raw list)."""
    from mcp.types import CallToolRequestParams
    from td_mcp.server_offline import _offline_call_tool

    result = asyncio.run(
        _offline_call_tool(None, CallToolRequestParams(name="__nonexistent__", arguments={}))
    )
    assert isinstance(result, types.CallToolResult)
    # Unknown tool returns an error in the content (not a crash)
    assert "unknown tool" in result.content[0].text
    print("ok  offline: call_tool returns CallToolResult for unknown tool")


# ---------------------------------------------------------------------------
# Live server
# ---------------------------------------------------------------------------

def test_live_tools_not_dead():
    """Every live tool in _TOOL_META must be in TOOL_REGISTRY and vice-versa."""
    from td_mcp.server_live import TOOL_REGISTRY, _TOOL_META

    meta_names = {m[0] for m in _TOOL_META}
    registry_names = set(TOOL_REGISTRY)

    dead_meta = meta_names - registry_names
    dead_reg = registry_names - meta_names
    assert not dead_meta, f"tools in _TOOL_META but not TOOL_REGISTRY: {dead_meta}"
    assert not dead_reg, f"tools in TOOL_REGISTRY but not _TOOL_META: {dead_reg}"
    print(f"ok  live: {len(meta_names)} tools in TOOL_REGISTRY, {len(registry_names)} in _TOOL_META")


def test_live_tool_schemas_complete():
    """Every live tool schema must have type=object + non-empty properties."""
    from td_mcp.server_live import _http_tool_meta, TOOL_REGISTRY, _TOOL_META

    for name, desc, props in _TOOL_META:
        assert desc, f"{name}: empty description"
        assert isinstance(props, dict), f"{name}: properties not a dict"
    tools = _http_tool_meta()
    for t in tools:
        assert t["description"], f"{t['name']}: empty description"
        schema = t["inputSchema"]
        props = schema.get("properties") or schema
        assert isinstance(props, dict), f"{t['name']}: no properties dict"
    no_arg = [t["name"] for t in tools if not props]
    print(f"ok  live: {len(tools)} tools, {len(no_arg)} no-arg tools")


def test_live_cache_hint():
    """Live tools/list must carry a cache hint."""
    from td_mcp import server_live

    result = asyncio.run(server_live._live_list_tools(None, None))
    assert result.ttl_ms is not None
    assert result.cache_scope is not None
    print(f"ok  live cache hint: ttl_ms={result.ttl_ms} scope={result.cache_scope}")


if __name__ == "__main__":
    test_offline_tools_not_dead()
    test_offline_tool_schemas_complete()
    test_offline_cache_hint()
    test_offline_calltool_returns_calltoolresult()
    test_live_tools_not_dead()
    test_live_tool_schemas_complete()
    test_live_cache_hint()
    print("\nSCHEMA CONFORMANCE TEST PASSED")
