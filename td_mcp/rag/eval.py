"""Eval harness: proves retrieval quality and stops regressions.

Metrics over labelled (query -> expected chunk ids) pairs:
  * recall@k      - fraction of expected chunks retrieved
  * MRR            - mean reciprocal rank of first expected hit
  * nDCG@k        - ranked-quality of the returned list

This is the differentiator vs other MIT TD RAGs: only TrueFiasco
ships an eval, and theirs is AGPL. Run often after KB/weight changes.

Usage:
  uv run python -m td_mcp.rag.eval
  TD_MCP_DENSE=1 uv run python -m td_mcp.rag.eval --k 5
"""

import os
import sys
import math

from td_mcp.rag.retriever import build_retriever
from td_mcp.rag.strategies import ParallelRetriever

QUERIES = [
    # --- operators (matched by name + nickname via aliases) ---
    {"q": "blur top parameters", "exp": ["blur_top"]},
    {"q": "movie file in top set the file parameter", "exp": ["movie_file_in_top", "moviefileintop_class"]},
    {"q": "read a video file into TOPs", "exp": ["movie_file_in_top"]},
    {"q": "movie", "exp": ["movie_file_in_top"]},                       # nickname
    {"q": "phong material lighting", "exp": ["phong_mat"]},
    {"q": "lfo oscillator chop", "exp": ["lfo_chop"]},
    {"q": "audio input chop", "exp": ["audio_device_in_chop"]},
    {"q": "box primitive geometry", "exp": ["box_sop"]},
    {"q": "container ui panel", "exp": ["container_comp"]},
    {"q": "table spreadsheet dat", "exp": ["table_dat"]},
    {"q": "osc receive network", "exp": ["osc_in_chop", "osc_in_dat"]},
    {"q": "touch in stream top", "exp": ["touch_in_top"]},

    # --- python API ---
    {"q": "create a child operator in python", "exp": ["op_class"]},
    {"q": "persist state with storage", "exp": ["tdstoretools_storage"]},
    {"q": "undo a mutation safely", "exp": ["ui_module"]},
    {"q": "wrap code in undo block", "exp": ["undo_safe_mutation_pattern", "ui_module"]},
    {"q": "parameter object par class", "exp": ["par_class"]},

    # --- glsl ---
    {"q": "GLSL fragment shader template", "exp": ["glsl_fragment_template"]},
    {"q": "glsl custom uniform binding", "exp": ["glsl_custom_uniforms"]},

    # --- tutorials / recipes ---
    {"q": "export chop channel to a parameter", "exp": ["chop_export_to_parameters"]},
    {"q": "audio reactive setup with chop", "exp": ["audio_reactive_recipe", "chop_export_to_parameters"]},
    {"q": "feedback trails recipe", "exp": ["feedback_top", "feedback_trails_recipe", "level_top"]},
    {"q": "wire operators together in python", "exp": ["connecting_operators_in_python"]},
    {"q": "render a 3d scene with camera and light", "exp": ["render_top", "camera_comp", "geometry_comp"]},
    {"q": "gpu instancing thousands of copies", "exp": ["instancing_recipe"]},
    {"q": "call a rest api from touchdesigner", "exp": ["web_api_request_recipe"]},
    {"q": "particle simulation with pops", "exp": ["pops_family_overview", "pop_solver", "particle_render_recipe_pops"]},

    # --- version gating (must_not checks) ---
    {"q": "particle operators on an old 2022 build", "exp": [], "version": "2022.10000",
     "must_not": ["pops_family_overview", "pop_solver", "particle_render_recipe_pops"]},
    {"q": "pbr material on 2022.10000", "exp": [], "version": "2022.10000",
     "must_not": ["pbr_mat", "environment_light_comp"]},

    # --- operator-parameter recall (plan sec.6) ---
    {"q": "blur top kernel width level parameters", "exp": ["blur_top", "corpus_blur_top"]},
    {"q": "movie file in top file parameter python", "exp": ["movie_file_in_top", "corpus_movie_file_in_top"]},
    {"q": "GLSL TOP custom uniform parameter page", "exp": ["glsl_top", "corpus_glsl_top"]},
    {"q": "feedback top feedback parameter trails", "exp": ["feedback_top", "corpus_feedback_top"]},
    {"q": "noise top type amplitude frequency parameters", "exp": ["noise_top", "corpus_noise_top"]},
    {"q": "render top camera light geometry parameters", "exp": ["render_top", "corpus_render_top"]},
    {"q": "timer chop length speed cue segments", "exp": ["timer_chop", "corpus_timer_chop"]},
    {"q": "box sop size center divisions parameters", "exp": ["box_sop", "corpus_box_sop"]},
    {"q": "phong mat color diffuse specular parameters", "exp": ["phong_mat", "corpus_phong_mat"]},
    {"q": "osc in chop network address port", "exp": ["osc_in_chop", "corpus_osc_in_chop"]},
]


def _dcg(rels):
    return sum(r / math.log2(i + 2) for i, r in enumerate(rels))


def _metrics(retrieved_ids, exp, must_not, k):
    # corpus_* variants are the same operator/class as the curated seed, so a
    # retrieved corpus chunk satisfies the same expected hit.
    variants = set(exp)
    for e in exp:
        variants.add("corpus_" + e)
        variants.add("corpus_py_" + e)
    hit = [rid for rid in retrieved_ids[:k] if rid in variants] if exp else []
    rec = (len(hit) / len(exp)) if exp else 1.0
    rec = min(rec, 1.0)
    rank = next((i + 1 for i, rid in enumerate(retrieved_ids[:k]) if rid in exp), None)
    mrr = (1.0 / rank) if rank else 0.0
    rel = [1.0 if rid in exp else 0.0 for rid in retrieved_ids[:k]]
    idcg = _dcg([1.0] * min(len(exp), k)) if exp else 1.0
    ndcg = _dcg(rel) / idcg if idcg > 0 else 0.0
    viol = [rid for rid in retrieved_ids[:k] if rid in (must_not or [])]
    return rec, mrr, ndcg, viol


def run(retriever, queries, ks=(1, 3, 5)):
    import math
    print(f"strategies: {[s.name() for s in retriever.strategies]}")
    for k in ks:
        recalls, mrrs, ndcgs = [], [], []
        violations = 0
        for q in queries:
            res = retriever.search(q["q"], version=q.get("version"), k=k)
            ids = [c["id"] for c, _ in res]
            rec, mrr, ndcg, viol = _metrics(ids, q.get("exp", []), q.get("must_not"), k)
            recalls.append(rec)
            mrrs.append(mrr)
            ndcgs.append(ndcg)
            violations += len(viol)
        n = len(queries)
        print(f"k={k:2d}  recall={sum(recalls)/n:.3f}  "
              f"MRR={sum(mrrs)/n:.3f}  nDCG={sum(ndcgs)/n:.3f}  "
              f"version_violations={violations}")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()

    ret = build_retriever()
    pr = ParallelRetriever(ret)
    print(f"KB chunks: {len(ret.chunks)}")
    run(pr, QUERIES, ks=(1, 3, args.k))


if __name__ == "__main__":
    main()
