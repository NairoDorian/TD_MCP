"""Unit tests for td_mcp.rag.retriever (Index + Retriever, pure stdlib).

Covers the empty-index regression (§4.1 of IMPROVEMENT_PLAN.md): when
``chunks.jsonl`` is missing — e.g. a fresh clone that hasn't run
``python -m td_mcp.kb.build_kb`` — the retriever must degrade to an empty
index that returns ``[]`` from every scorer and ``Retriever.search`` instead
of raising ``IndexError``.
"""

from td_mcp.rag.retriever import Index, Retriever


def test_empty_index_does_not_crash():
    idx = Index([])
    assert idx.N == 0
    qt = ["blur", "top", "parameters"]
    # Every scorer must return [] of the right length (0) instead of IndexError.
    assert idx.bm25(qt) == []
    assert idx.tfidf_dense(qt) == []
    assert idx.dense("blur top") == []
    assert idx.title_scores(qt) == []


def test_empty_index_search_returns_empty():
    r = Retriever([])
    assert r.search("blur top parameters", k=5) == []


def test_empty_index_search_short_circuits_on_empty_query():
    r = Retriever([])
    # Empty/whitespace query has no tokens; must return [] without touching
    # the (empty) index.
    assert r.search("", k=5) == []
    assert r.search("   ", k=3) == []


if __name__ == "__main__":
    test_empty_index_does_not_crash()
    test_empty_index_search_returns_empty()
    test_empty_index_search_short_circuits_on_empty_query()
    print("\nALL RETRIEVER TESTS PASSED")
