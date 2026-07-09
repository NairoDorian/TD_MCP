"""Test agent script compilation and schemas."""

from bridge.td_mcp_agent import TOOLS_SCHEMA, _execute_tool


def test_agent_schema():
    assert len(TOOLS_SCHEMA) > 10
    names = [t["function"]["name"] for t in TOOLS_SCHEMA]
    assert "create_node" in names
    assert "build_and_verify" in names
    for n in ["connect_nodes", "rename_node", "copy_node", "auto_layout",
              "get_node", "set_node_color", "set_node_comment", "map_network", "save_tox",
              "disconnect_nodes", "get_connections", "exec_node_method", "snapshot_network",
              "restore_network", "get_performance", "validate_network", "set_flags",
              "find_nodes", "set_node_position", "timeline", "export_recipe", "import_recipe",
              "caption_viewport"]:
        assert n in names, n
    print("ok  agent schemas compile correctly and include extended tools")


def test_tool_execution_mock():
    # If the bridge is not running, _execute_tool should fail gracefully returning {"ok": False}
    res = _execute_tool("project_info", {}, port=9999)  # unused port
    assert res["ok"] is False
    assert "error" in res
    print("ok  tool execution handles errors gracefully when bridge is offline")
