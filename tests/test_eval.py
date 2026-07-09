"""Test eval harness."""

import json
from td_mcp.eval import RetrievalMetrics, RetrievalEvalResult, RetrievalEvalResult, TrendGate, RETRIEVAL_GOLDEN_SET


def test_retrieval_metrics():
    # Perfect recall
    m = RetrievalMetrics(query="test", relevant_ids=["a", "b"], retrieved_ids=["a", "b", "c"], k=3)
    assert m.recall_at_k() == 1.0
    assert m.mrr() == 1.0

    # Partial recall
    m = RetrievalMetrics(query="test", relevant_ids=["a", "b", "c"], retrieved_ids=["a", "d", "e"], k=3)
    assert m.recall_at_k() == 1/3
    assert m.mrr() == 1.0  # first hit at rank 1

    # No hits
    m = RetrievalMetrics(query="test", relevant_ids=["a", "b"], retrieved_ids=["x", "y", "z"], k=3)
    assert m.recall_at_k() == 0.0
    assert m.mrr() == 0.0

    print("ok  RetrievalMetrics basic")


def test_retrieval_eval_result():
    queries = [
        {"query": "q1", "relevant_ids": ["a", "b"]},
        {"query": "q2", "relevant_ids": ["c"]},
    ]
    def dummy_retriever(q):
        return ["a", "x", "y"] if q == "q1" else ["c", "d"]
    res = RetrievalEvalResult.from_queries(queries, dummy_retriever, k=3)
    assert len(res.metrics_per_query) == 2
    assert 0 < res.mean_recall_at_k <= 1
    assert 0 <= res.mean_mrr <= 1
    print("ok  RetrievalEvalResult aggregation")


def test_trend_gate():
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({"mean_recall_at_k": 0.9, "mean_mrr": 0.8, "mean_ndcg_at_k": 0.85}, f)
        baseline_path = f.name

    # Current better than baseline -> pass
    class MockResult:
        mean_recall_at_k = 0.95
        mean_mrr = 0.85
        mean_ndcg_at_k = 0.9

    gate = TrendGate(baseline_path, threshold=0.05)
    result = gate.check(MockResult())
    assert result["passed"] is True
    assert len(result["improvements"]) == 3

    # Current worse than baseline -> fail
    class BadResult:
        mean_recall_at_k = 0.8
        mean_mrr = 0.7
        mean_ndcg_at_k = 0.75

    gate2 = TrendGate(baseline_path, threshold=0.05)
    result2 = gate2.check(BadResult())
    assert result2["passed"] is False
    assert len(result2["regressions"]) == 3

    print("ok  TrendGate")


def test_golden_set():
    assert len(RETRIEVAL_GOLDEN_SET) >= 10
    for q in RETRIEVAL_GOLDEN_SET:
        assert "query" in q
        assert "relevant_ids" in q
        assert isinstance(q["relevant_ids"], list)
    print("ok  RETRIEVAL_GOLDEN_SET")


if __name__ == "__main__":
    test_retrieval_metrics()
    test_retrieval_eval_result()
    test_trend_gate()
    test_golden_set()
    print("All eval tests passed")