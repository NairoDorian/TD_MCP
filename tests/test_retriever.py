"""Unit tests for td_mcp.rag.retriever (Index + Retriever, pure stdlib).

Covers the empty-index regression (§4.1 of IMPROVEMENT_PLAN.md): when
``chunks.jsonl`` is missing — e.g. a fresh clone that hasn't run
``python -m td_mcp.kb.build_kb`` — the retriever must degrade to an empty
index that returns ``[]`` from every scorer and ``Retriever.search`` instead
of raising ``IndexError``.

Also covers the experimental-technique regression (RAG audit G2): the
bottobot technique library must be indexed as retriever chunks so it is no
longer siloed behind ``td_docs_experimental`` alone. Previously the 7
technique category files lived only in ``kb/corpus/experimental/`` and were
read by ``td_docs_experimental`` but never emitted into ``chunks.jsonl`` --
they were invisible to BM25/RRF, score-floor abstention, version
reconciliation and the integrity receipt. ``corpus.experimental_technique_chunks``
(now called by ``corpus_chunks`` and thus by ``build_kb``) turns each technique
into a ``category: technique`` chunk.
"""

from td_mcp.rag.retriever import Index, Retriever
from td_mcp.kb import corpus as corpus_mod


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


def test_experimental_techniques_are_indexed():
    chunks = corpus_mod.experimental_technique_chunks()
    assert len(chunks) > 0
    ids = {c["id"] for c in chunks}
    # A known technique id from the glsl category.
    assert "tech_glsl_raymarching_basic" in ids
    # Every technique chunk carries the expected retriever shape + a category.
    for c in chunks:
        assert c["category"] == "technique"
        assert c["text"]
        assert c["min_version"] == "all"  # authored content, not a TD snapshot


if __name__ == "__main__":
    test_empty_index_does_not_crash()
    test_empty_index_search_returns_empty()
    test_empty_index_search_short_circuits_on_empty_query()
    test_experimental_techniques_are_indexed()
    print("\nALL RETRIEVER TESTS PASSED")
