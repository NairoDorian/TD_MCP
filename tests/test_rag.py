"""RAG regression + multi-process fusion tests.

Run:  uv run python -m tests.test_rag
"""

import sys

from td_mcp.rag.retriever import build_retriever
from td_mcp.rag.strategies import ParallelRetriever, RemoteMCPStrategy


class _FakeReranker:
    """Deterministic reranker: reverses the candidate order so we can prove
    that the original per-document RRF score stays attached to the right doc
    after reordering (regression test for the score-misassignment bug)."""
    def rerank(self, query, chunks):
        return list(chunks)[::-1]


def _pr():
    return ParallelRetriever(build_retriever())


def test_basic_recall():
    pr = _pr()
    res = pr.search("blur top parameters", k=3)
    ids = [c["id"] for c, _ in res]
    assert "blur_top" in ids, ids
    print("ok  basic blur_top in top3:", ids[:3])


def test_version_filter():
    pr = _pr()
    res = pr.search("particle operators", version="2022.10000", k=5)
    ids = [c["id"] for c, _ in res]
    assert "pops_family_overview" not in ids, f"pops leaked into a 2022 build: {ids}"
    print("ok  version filter excludes POPs on 2022:", ids)


def test_per_source_fusion():
    pr = _pr()
    res = pr.search("GLSL fragment shader template", k=3)
    ids = [c["id"] for c, _ in res]
    assert "glsl_fragment_template" in ids, ids
    print("ok  glsl source fused:", ids[:3])


def test_rerank_keeps_scores():
    # The reranker reverses order; each doc must keep its OWN rrf score,
    # not the score of the doc that landed in its new position.
    full = ParallelRetriever(build_retriever()).search("blur top parameters", k=1000)
    expected = {c["id"]: s for c, s in full}

    pr = ParallelRetriever(build_retriever(), reranker=_FakeReranker(), top_m=15)
    res = pr.search("blur top parameters", k=15)
    for c, s in res:
        assert s is not None and s >= 0.0, (c["id"], s)
        # score must equal the doc's own original rrf score (the bug reassigned
        # by position, so this would mismatch)
        assert s == expected.get(c["id"]), (c["id"], s, expected.get(c["id"]))
    print("ok  rerank preserves per-doc scores:", [(c["id"], round(s, 4)) for c, s in res[:3]])


def test_remote_fusion():
    # stub the remote stdio round-trip with a canned result so we
    # exercise the RRF document-URI fusion without a real server
    class Stub(RemoteMCPStrategy):
        def candidates(self, qt, query, query_vec, k):
            key = "remote:123"
            self.remote_docs[key] = {
                "id": key, "source": "remote:search_touchdesigner_docs",
                "title": "FAKE REMOTE RESULT for particle",
                "text": "synthetic external doc",
            }
            return [key]

    pr = ParallelRetriever(build_retriever())
    pr.remote = Stub(["python", "-m", "tests.fake_remote_mcp"])
    pr.strategies.append(pr.remote)

    res = pr.search("particle operators", k=len(pr.index.chunks) + 5)
    has_local = any(c.get("source", "").startswith("docs.") for c, _ in res)
    has_remote = any(str(c.get("source", "")).startswith("remote:") for c, _ in res)
    assert has_local and has_remote, [(c.get("source"), s) for c, s in res]
    print("ok  fused local + remote:", [(c.get("source"), round(s, 4)) for c, s in res[:4]])


if __name__ == "__main__":
    test_basic_recall()
    test_version_filter()
    test_per_source_fusion()
    test_rerank_keeps_scores()
    test_remote_fusion()
    print("\nALL RAG TESTS PASSED")
