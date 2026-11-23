"""Argument-name parity across the three surfaces a tool call crosses.

`test_parity.py` proves the *tool names* agree between the advertised schema,
the live registry and the bridge. Nothing proved the *argument names* did —
and a schema that says `type` while the handler reads `op_type` is a tool the
model can see, call correctly, and still have fail with "Unknown operator
type. Value:None". Found while driving `td-mcp-live` as a real MCP client
against a live TouchDesigner (2026-08-26).

Three checks, all static (AST), so nothing here needs TouchDesigner:

1. MCP schema (`_TOOL_META`) ⇄ bridge handler (`_DISPATCH`): since 1.13 the
   registry forwards arguments unchanged, so every advertised property must
   be read by the bridge, and every key the bridge reads must be advertised.
2. Client payload (`TDClient` methods' `_call(tool, {...})`) ⊆ bridge handler
   (`_DISPATCH[tool]` lambda's `a.get(...)`): every key the client sends is a
   key the bridge reads, or it is silently dropped.
3. Every bridge `_DISPATCH` lambda reads only keys the client can send.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from td_mcp.server_live import _TOOL_META

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "td_mcp" / "server_live.py"
BRIDGE = ROOT / "bridge" / "td_mcp_bridge.py"

#: Keys a lambda reads that the schema deliberately does not advertise, or
#: vice versa. Each entry needs a reason.
KNOWN_EXCEPTIONS = {
    # Accepted for clients written against the old schema; `type` is primary.
    ("build_and_verify", "op_type"),
    # `type` accepted as an alias so the create_node spelling also works.
    ("describe_op_type", "type"),
}


def _a_get_keys(node: ast.AST) -> set[str]:
    """Constant keys of every `a.get("key" ...)` under `node`."""
    keys = set()
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "get" and isinstance(sub.func.value, ast.Name)
                and sub.func.value.id == "a" and sub.args
                and isinstance(sub.args[0], ast.Constant)):
            keys.add(sub.args[0].value)
    return keys


def _dict_assign(tree: ast.Module, name: str) -> ast.Dict:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            assert isinstance(node.value, ast.Dict), name
            return node.value
    raise AssertionError(f"{name} not found")


def _bridge_keys() -> dict[str, set[str]]:
    tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
    d = _dict_assign(tree, "_DISPATCH")
    return {k.value: _a_get_keys(v) for k, v in zip(d.keys, d.values)}


def _client_payload_keys() -> dict[str, set[str]]:
    """For each TDClient method: the keys of the dict it passes to _call."""
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "TDClient":
            for fn in node.body:
                if not isinstance(fn, ast.FunctionDef):
                    continue
                for call in ast.walk(fn):
                    if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                            and call.func.attr == "_call" and call.args
                            and isinstance(call.args[0], ast.Constant)):
                        tool = call.args[0].value
                        keys = set()
                        if len(call.args) > 1 and isinstance(call.args[1], ast.Dict):
                            keys = {k.value for k in call.args[1].keys if isinstance(k, ast.Constant)}
                        out.setdefault(tool, set()).update(keys)
    return out


SCHEMA = {name: set(props) for name, _desc, props in _TOOL_META}


#: The live registry forwards arguments unchanged (1.13), renaming only these.
_FORWARD_RENAMES = {"detail": "detailLevel"}
#: Tools answered on the client side, and keys consumed before dispatch.
_CLIENT_ONLY = {"get_non_default_params"}
_PRE_DISPATCH = {
    # read by the bridge's multi-frame capture path (_capture_any_family),
    # which runs before the single-frame handler is dispatched
    ("capture_viewport", "width"),
    # read by the multi-frame performance path (_perf_across_frames)
    ("get_performance", "samples"),
}


class TestSchemaMatchesBridge:
    """Since 1.13 the registry forwards every argument as-is, so the contract
    is schema <-> bridge handler: an advertised key the bridge never reads is
    silently dropped, and a key the bridge reads but the schema hides is a
    feature no client can reach (create_node's nodeX/nodeY were exactly that)."""

    @pytest.mark.parametrize("tool", sorted(set(SCHEMA) - _CLIENT_ONLY))
    def test_schema_properties_and_bridge_keys_agree(self, tool):
        bridge = _bridge_keys()
        assert tool in bridge, f"{tool} advertised but the bridge does not dispatch it"
        advertised = {_FORWARD_RENAMES.get(k, k) for k in SCHEMA[tool]}
        read = bridge[tool]
        unread = {k for k in advertised - read
                  if (tool, k) not in KNOWN_EXCEPTIONS and (tool, k) not in _PRE_DISPATCH}
        hidden = {k for k in read - advertised if (tool, k) not in KNOWN_EXCEPTIONS}
        assert not unread, f"{tool}: schema advertises {sorted(unread)} but the bridge never reads them"
        assert not hidden, f"{tool}: bridge reads {sorted(hidden)} but the schema does not advertise them"


def test_registry_forwards_arguments_unchanged():
    """The forwarding lambda passes every key through (detail renamed)."""
    from td_mcp import server_live

    seen = {}

    class _Recorder:
        def _call(self, tool, args=None):
            seen[tool] = args
            return {"ok": True}

    orig = server_live._get_client
    server_live._get_client = lambda: _Recorder()
    try:
        server_live.TOOL_REGISTRY["create_node"](
            {"path": "/project1", "type": "noiseTOP", "nodeX": 200, "nodeY": -400})
        server_live.TOOL_REGISTRY["get_parameters"](
            {"path": "/project1/n", "names": "period", "non_default": True, "detail": "full"})
    finally:
        server_live._get_client = orig
    assert seen["create_node"] == {"path": "/project1", "type": "noiseTOP",
                                   "nodeX": 200, "nodeY": -400}
    assert seen["get_parameters"] == {"path": "/project1/n", "names": "period",
                                      "non_default": True, "detailLevel": "full"}


class TestClientMatchesBridge:
    @pytest.mark.parametrize("tool", sorted(_client_payload_keys()))
    def test_every_key_the_client_sends_is_read_by_the_bridge(self, tool):
        bridge = _bridge_keys()
        assert tool in bridge, f"client calls {tool!r}, which the bridge does not dispatch"
        sent = _client_payload_keys()[tool]
        dropped = sent - bridge[tool]
        assert not dropped, f"{tool}: client sends {sorted(dropped)} but the bridge handler ignores them"


def test_the_extractor_sees_real_keys():
    """Guard against the AST helpers silently returning empty sets."""
    assert _bridge_keys()["get_errors"] >= {"path", "recurse"}
    assert _client_payload_keys()["get_errors"] >= {"path", "recurse"}
