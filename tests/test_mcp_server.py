"""Server wiring test (framework-transport independent).

The mcp 1.28.1 stdio transport raises an internal pydantic
error on this pydantic build, so we test the server wiring
directly: create_server() must build (decorators run), and the
tool implementations must return KB content. The live transport
is exercised by the `td-mcp-offline` console script.

Run:  uv run python -m tests.test_mcp_server
"""

from mcp.server import Server

from td_mcp.server_offline import create_server, td_docs_search, get_pr
from td_mcp.rag.strategies import ParallelRetriever


def test_wiring():
    app = create_server()
    assert isinstance(app, Server), type(app)
    print("ok  create_server() builds a Server (decorators registered)")


def test_tool_implementation():
    out = td_docs_search("blur top parameters")
    assert "Blur TOP" in out, out[:200]
    print("ok  td_docs_search returns KB chunk (Blur TOP)")


def test_six_strategies():
    pr = get_pr()
    assert isinstance(pr, ParallelRetriever)
    assert len(pr.strategies) == 6, [s.name() for s in pr.strategies]
    print("ok  6 fusion strategies wired:", [s.name() for s in pr.strategies])


if __name__ == "__main__":
    test_wiring()
    test_tool_implementation()
    test_six_strategies()
    print("\nMCP SERVER TEST PASSED")
