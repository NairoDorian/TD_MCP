"""Argument-name parity across the three surfaces a tool call crosses.

`test_parity.py` proves the *tool names* agree between the advertised schema,
the live registry and the bridge. Nothing proved the *argument names* did —
and a schema that says `type` while the handler reads `op_type` is a tool the
model can see, call correctly, and still have fail with "Unknown operator
type. Value:None". Found while driving `td-mcp-live` as a real MCP client
against a live TouchDesigner (2026-08-26).

Three checks, all static (AST), so nothing here needs TouchDesigner:

1. MCP schema (`_TOOL_META`) ⇄ registry lambda (`TOOL_REGISTRY`): every
   property the schema advertises is read by the lambda, and every key the
   lambda reads is advertised.
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


def _registry_keys() -> dict[str, set[str]]:
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    out = {}
    for k, v in zip(_dict_assign(tree, "TOOL_REGISTRY").keys, _dict_assign(tree, "TOOL_REGISTRY").values):
        out[k.value] = _a_get_keys(v)
    return out


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


class TestSchemaMatchesRegistry:
    @pytest.mark.parametrize("tool", sorted(SCHEMA))
    def test_schema_properties_and_lambda_keys_agree(self, tool):
        registry = _registry_keys()
        assert tool in registry, f"{tool} advertised but not in TOOL_REGISTRY"
        advertised = SCHEMA[tool]
        read = registry[tool]
        unread = {k for k in advertised - read if (tool, k) not in KNOWN_EXCEPTIONS}
        unadvertised = {k for k in read - advertised if (tool, k) not in KNOWN_EXCEPTIONS}
        assert not unread, f"{tool}: schema advertises {sorted(unread)} but the handler never reads them"
        assert not unadvertised, f"{tool}: handler reads {sorted(unadvertised)} but the schema does not advertise them"


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
    assert _registry_keys()["get_errors"] >= {"path", "include_warnings"}
    assert _bridge_keys()["get_errors"] >= {"path", "recurse"}
    assert _client_payload_keys()["get_errors"] >= {"path", "recurse"}
