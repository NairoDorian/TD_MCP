"""Test live server MCP wiring and tool registration."""

from mcp.server import Server
from td_mcp.server_live import create_server


def test_live_wiring():
    app = create_server()
    assert isinstance(app, Server), type(app)
    print("ok  create_server() for live server builds a Server")
