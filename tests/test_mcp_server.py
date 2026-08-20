"""Server wiring test (framework-transport independent).

The mcp 1.28.1 stdio transport raises an internal pydantic
error on this pydantic build, so we test the server wiring
directly: create_server() must build (decorators run), and the
tool implementations must return KB content. The live transport
is exercised by the `td-mcp-offline` console script.

Run:  uv run python -m tests.test_mcp_server
"""

import os

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


def test_fusion_strategies_wired():
    """Global BM25 + title boost + one per-source BM25 per chunk category.

    The count is data-dependent — a KB built with a local wiki mirror adds a
    ``concept`` category — so assert the composition, not a magic number.
    """
    pr = get_pr()
    assert isinstance(pr, ParallelRetriever)
    names = [s.name() for s in pr.strategies]
    assert "BM25Strategy" in names, names
    assert "TitleStrategy" in names, names
    categories = {c.get("category") for c in pr.index.chunks if c.get("category")}
    for cat in categories:
        assert f"BM25[{cat}]" in names, (cat, names)
    assert len(pr.strategies) == 2 + len(categories), names
    print(f"ok  {len(names)} fusion strategies wired:", names)


def test_citation_url_in_search():
    out = td_docs_search("blur top parameters", k=1)
    assert "link: https://docs.derivative.ca/" in out, out[:300]


def test_disambiguation_in_search():
    if not os.path.exists(os.path.join(os.path.dirname(__file__), "..",
                                       "td_mcp", "kb", "corpus", "operators.json")):
        return
    from td_mcp.server_offline import td_docs_operator
    out = td_docs_operator("Noise")
    assert "Ambiguous" in out


if __name__ == "__main__":
    test_wiring()
    test_tool_implementation()
    test_fusion_strategies_wired()
    print("\nMCP SERVER TEST PASSED")
