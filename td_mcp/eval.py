"""Evaluation harness for td-mcp (TD_Builder_alpha style).

Provides:
- Retrieval metrics: recall@k, MRR, nDCG
- Build correctness: node count, wiring, param accuracy, cook success
- Trend gate: blocks PRs that regress metrics below threshold
- CI-friendly JSON output
"""

import json
import os
import time
import statistics
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable
from datetime import datetime


@dataclass
class RetrievalMetrics:
    """Retrieval quality metrics for a single query."""
    query: str
    relevant_ids: List[str]
    retrieved_ids: List[str]
    k: int

    def recall_at_k(self) -> float:
        if not self.relevant_ids:
            return 1.0
        hits = sum(1 for r in self.retrieved_ids[:self.k] if r in self.relevant_ids)
        return hits / len(self.relevant_ids)

    def mrr(self) -> float:
        """Mean Reciprocal Rank."""
        for i, r in enumerate(self.retrieved_ids, 1):
            if r in self.relevant_ids:
                return 1.0 / i
        return 0.0

    def ndcg_at_k(self) -> float:
        """Normalized Discounted Cumulative Gain @ k."""
        if not self.relevant_ids:
            return 1.0
        # Ideal DCG: all relevant at top
        ideal_dcg = sum(1.0 / (i + 1) for i in range(min(len(self.relevant_ids), self.k)))
        dcg = 0.0
        for i, r in enumerate(self.retrieved_ids[:self.k], 1):
            if r in self.relevant_ids:
                dcg += 1.0 / (i + 1)
        return dcg / ideal_dcg if ideal_dcg > 0 else 1.0


@dataclass
class RetrievalEvalResult:
    """Aggregate retrieval evaluation results."""
    metrics_per_query: List[RetrievalMetrics]
    mean_recall_at_k: float
    mean_mrr: float
    mean_ndcg_at_k: float
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    @classmethod
    def from_queries(cls, queries: List[Dict], retriever_fn: Callable[[str], List[str]], k: int = 5):
        """Run evaluation on a list of queries with ground truth."""
        metrics = []
        for q in queries:
            retrieved = retriever_fn(q["query"])
            metrics.append(RetrievalMetrics(
                query=q["query"],
                relevant_ids=q["relevant_ids"],
                retrieved_ids=retrieved,
                k=k
            ))
        mean_recall = statistics.mean(m.recall_at_k() for m in metrics) if metrics else 0
        mean_mrr = statistics.mean(m.mrr() for m in metrics) if metrics else 0
        mean_ndcg = statistics.mean(m.ndcg_at_k() for m in metrics) if metrics else 0
        return cls(
            metrics_per_query=metrics,
            mean_recall_at_k=mean_recall,
            mean_mrr=mean_mrr,
            mean_ndcg_at_k=mean_ndcg
        )

    def to_dict(self) -> Dict:
        return {
            "mean_recall_at_k": self.mean_recall_at_k,
            "mean_mrr": self.mean_mrr,
            "mean_ndcg_at_k": self.mean_ndcg_at_k,
            "timestamp": self.timestamp,
            "per_query": [
                {
                    "query": m.query,
                    "recall_at_k": m.recall_at_k(),
                    "mrr": m.mrr(),
                    "ndcg_at_k": m.ndcg_at_k()
                }
                for m in self.metrics_per_query
            ]
        }


@dataclass
class BuildEvalResult:
    """Build correctness evaluation for a generated network."""
    spec_name: str
    expected_nodes: int
    actual_nodes: int
    expected_edges: int
    actual_edges: int
    param_accuracy: float  # fraction of params set correctly
    cook_success: bool
    viewport_verdict: Optional[str]  # "pass" | "black" | "flat" | "error"
    errors: List[str]
    duration_sec: float
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class EvalReport:
    """Complete evaluation report for a run."""
    retrieval: Optional[RetrievalEvalResult] = None
    builds: List[BuildEvalResult] = field(default_factory=list)
    trend_gate_passed: bool = True
    trend_details: Dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_json(self) -> str:
        return json.dumps({
            "retrieval": self.retrieval.to_dict() if self.retrieval else None,
            "builds": [b.to_dict() for b in self.builds],
            "trend_gate_passed": self.trend_gate_passed,
            "trend_details": self.trend_details,
            "timestamp": self.timestamp
        }, indent=2)

    def save(self, path: str):
        Path(path).write_text(self.to_json())


class TrendGate:
    """Trend gate: compare current metrics against baseline, fail if regression > threshold."""

    def __init__(self, baseline_path: str, threshold: float = 0.02):
        """
        Args:
            baseline_path: Path to JSON file with baseline metrics
            threshold: Max allowed regression (e.g., 0.02 = 2% drop)
        """
        self.baseline_path = Path(baseline_path)
        self.threshold = threshold
        self.baseline = self._load_baseline()

    def _load_baseline(self) -> Dict:
        if self.baseline_path.exists():
            return json.loads(self.baseline_path.read_text())
        return {}

    def check(self, current: RetrievalEvalResult) -> Dict:
        """Compare current metrics against baseline."""
        results = {
            "passed": True,
            "regressions": [],
            "improvements": [],
            "current": {
                "recall_at_k": current.mean_recall_at_k,
                "mrr": current.mean_mrr,
                "ndcg_at_k": current.mean_ndcg_at_k
            },
            "baseline": {}
        }

        for metric in ["mean_recall_at_k", "mean_mrr", "mean_ndcg_at_k"]:
            baseline_val = self.baseline.get(metric, 0)
            current_val = getattr(current, metric, 0)
            results["baseline"][metric] = baseline_val
            if baseline_val > 0:
                regression = (baseline_val - current_val) / baseline_val
                if regression > self.threshold:
                    results["passed"] = False
                    results["regressions"].append({
                        "metric": metric,
                        "baseline": baseline_val,
                        "current": current_val,
                        "regression_pct": round(regression * 100, 2)
                    })
                elif current_val > baseline_val:
                    results["improvements"].append({
                        "metric": metric,
                        "baseline": baseline_val,
                        "current": current_val,
                        "improvement_pct": round((current_val - baseline_val) / baseline_val * 100, 2)
                    })

        return results

    def update_baseline(self, current: RetrievalEvalResult):
        """Update baseline with current metrics (call after successful gate)."""
        self.baseline = {
            "mean_recall_at_k": current.mean_recall_at_k,
            "mean_mrr": current.mean_mrr,
            "mean_ndcg_at_k": current.mean_ndcg_at_k,
            "updated": datetime.utcnow().isoformat()
        }
        self.baseline_path.write_text(json.dumps(self.baseline, indent=2))


# ---------------------------------------------------------------------------
# Built-in test suites
# ---------------------------------------------------------------------------
RETRIEVAL_GOLDEN_SET = [
    # TD operator docs
    {"query": "Noise TOP parameters", "relevant_ids": ["Noise_TOP", "TOP_Class"]},
    {"query": "CHOP execute python", "relevant_ids": ["CHOP_Execute_CHOP", "CHOP_Execute_Class"]},
    {"query": "GLSL shader uniform", "relevant_ids": ["GLSL_TOP", "GLSL_Class", "uniform"]},
    {"query": "container COMP children", "relevant_ids": ["Container_COMP", "COMP_Class"]},
    {"query": "select CHOP channels", "relevant_ids": ["Select_CHOP"]},
    {"query": "math CHOP range", "relevant_ids": ["Math_CHOP", "from_range", "to_range"]},
    {"query": "feedback loop TOP", "relevant_ids": ["Feedback_TOP", "TOP_Class"]},
    {"query": "audio reactive network", "relevant_ids": ["Audio_Device_In_CHOP", "CHOP_Class"]},
    {"query": "particle system GPU", "relevant_ids": ["Point_POP", "GLSL_TOP"]},
    {"query": "DMX output sACN", "relevant_ids": ["DMX_Out_CHOP", "ArtNet_CHOP"]},
]


def run_retrieval_eval(retriever_fn: Callable[[str], List[str]], k: int = 5,
                       queries: Optional[List[Dict]] = None) -> RetrievalEvalResult:
    """Run retrieval evaluation on golden set or custom queries."""
    return RetrievalEvalResult.from_queries(
        queries or RETRIEVAL_GOLDEN_SET,
        retriever_fn,
        k=k
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="td-mcp evaluation harness")
    ap.add_argument("--retrieval", action="store_true", help="Run retrieval eval")
    ap.add_argument("--baseline", default=".eval_baseline.json", help="Baseline file")
    ap.add_argument("--threshold", type=float, default=0.02, help="Regression threshold")
    ap.add_argument("--k", type=int, default=5, help="Recall@k")
    ap.add_argument("--output", default="eval_report.json", help="Output report file")
    args = ap.parse_args()

    if args.retrieval:
        # This would need an actual retriever - placeholder for now
        print("Retrieval eval requires a retriever_fn - import and call run_retrieval_eval() directly")
        return

    print("Use --retrieval with a retriever function, or import eval functions directly.")


if __name__ == "__main__":
    _cli()