"""RAG wave-9 tests: plural stemming, operator boost precision, family/tutorial
boosts, sibling disambiguation, and KB stats."""

import json

from td_mcp.rag.retriever import build_retriever
from td_mcp.rag.strategies import ParallelRetriever
from td_mcp.rag.knowledge_graph import sibling_operators
from td_mcp.server_offline import td_kb_stats


def _pr():
    return ParallelRetriever(build_retriever())


def test_plural_stemming_pops():
    # "pops" must stem to "pop" so POP operator chunks match.
    pr = _pr()
    res = pr.search("particle simulation with pops", k=8)
    ids = [c["id"] for c, _ in res]
    assert "pop_solver" in ids, ids
    assert "pops_family_overview" in ids, ids


def test_audio_reactive_recipe():
    # "audio reactive" must boost the tutorial recipe and export path.
    pr = _pr()
    res = pr.search("audio reactive setup with chop", k=8)
    ids = [c["id"] for c, _ in res]
    assert "audio_reactive_recipe" in ids, ids
    assert "chop_export_to_parameters" in ids, ids


def test_glsl_top_dual_hit():
    # Both the curated glsl_top and the corpus glsl_top should surface.
    pr = _pr()
    res = pr.search("GLSL TOP custom uniform parameter page", k=8)
    ids = [c["id"] for c, _ in res]
    assert "glsl_top" in ids, ids
    assert "corpus_glsl_top" in ids, ids


def test_full_eval_recall():
    """The complete labelled eval set must hit perfect recall at k=5."""
    from td_mcp.rag.eval import QUERIES, _metrics
    pr = _pr()
    bad = []
    for q in QUERIES:
        res = pr.search(q["q"], version=q.get("version"), k=5)
        ids = [c["id"] for c, _ in res]
        rec, _, _, viol = _metrics(ids, q.get("exp", []), q.get("must_not"), 5)
        if rec < 1.0 or viol:
            bad.append((q["q"], ids, q.get("exp"), viol))
    assert not bad, bad


def test_sibling_operators():
    assert "Noise CHOP" in sibling_operators("Noise TOP")
    assert "Noise POP" in sibling_operators("Noise TOP")
    assert sibling_operators("Blur TOP") == []


def test_kb_stats():
    out = td_kb_stats()
    data = json.loads(out)
    assert data["chunks"] > 1000
    assert "TOP" in data["families"]
    assert data["operators"] > 600
