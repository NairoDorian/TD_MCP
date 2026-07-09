"""Test live server MCP wiring and tool registration."""

import mcp.types as types
from mcp.server import Server
from td_mcp.server_live import create_server, TDClient


def test_live_wiring():
    app = create_server()
    assert isinstance(app, Server), type(app)
    print("ok  create_server() for live server builds a Server")


def test_live_registered_handlers():
    app = create_server()
    keys = {k.__name__ for k in app.request_handlers}
    # Core + newly added live tools/prompts/resources
    for name in ["ListToolsRequest", "CallToolRequest", "ListPromptsRequest",
                 "GetPromptRequest", "ListResourceTemplatesRequest", "ReadResourceRequest"]:
        assert name in keys, name
    print("ok  live server registers tool/prompt/resource handlers")


def test_live_client_has_new_methods():
    for m in ["connect_nodes", "rename_node", "copy_node", "auto_layout",
              "get_node", "set_node_color", "set_node_comment", "map_network", "save_tox",
              "disconnect_nodes", "get_connections", "exec_node_method", "snapshot_network",
              "restore_network", "get_performance", "validate_network", "set_flags",
              "find_nodes", "set_node_position", "timeline", "export_recipe", "import_recipe"]:
        assert hasattr(TDClient, m), m
    print("ok  TDClient exposes the full bridge tool surface")


def test_live_tool_names():
    # Indirectly confirm dispatch knows the new tools by checking the handler exists.
    from td_mcp.server_live import create_server
    app = create_server()
    # The call_tool handler is registered under CallToolRequest.
    assert types.CallToolRequest in app.request_handlers
    print("ok  CallToolRequest handler present for new tools")
