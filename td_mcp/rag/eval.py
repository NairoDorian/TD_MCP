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
    {"q": "movie file in top set the file parameter",
     "exp": [["movie_file_in_top", "moviefileintop_class"]]},
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
    # A list *inside* exp is an OR-group: the official CHOP Export wiki page
    # and the curated recipe are both correct canonical answers.
    {"q": "export chop channel to a parameter",
     "exp": [["chop_export_to_parameters", "chop_export"]]},
    {"q": "audio reactive setup with chop", "exp": ["audio_reactive_recipe", "chop_export_to_parameters"]},
    {"q": "feedback trails recipe", "exp": ["feedback_top", "feedback_trails_recipe", "level_top"]},
    {"q": "wire operators together in python", "exp": ["connecting_operators_in_python"]},
    {"q": "render a 3d scene with camera and light",
     "exp": ["render_top", "camera_comp", "light_comp"]},
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
    {"q": "movie file in top file parameter python", "exp": ["movie_file_in_top"]},

    # --- wiki coverage (full local docs mirror, section-level chunks) ---
    # `needs: "wiki"` queries are skipped when the KB was built without a wiki
    # mirror (a fresh clone / CI), since their answers only exist in it.
    {"q": "blur top filter size pixels", "exp": ["blur_top"], "needs": "wiki"},
    {"q": "what is cooking in touchdesigner", "exp": ["cook"], "needs": "wiki"},
    {"q": "perform mode fullscreen", "needs": "wiki",
     "exp": [["perform_mode", "perform_mode_windowing"]]},
    {"q": "widgets ui building", "exp": ["widgets"], "needs": "wiki"},
    {"q": "python working with ops", "exp": ["working_with_ops_in_python"], "needs": "wiki"},
    {"q": "timeline frame rate settings", "exp": [["frame_rate", "timeline"]], "needs": "wiki"},
    {"q": "optimize performance monitor", "exp": [["optimize", "category_optimize"]], "needs": "wiki"},
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


def _same_doc(rid, exp_id):
    """True when a retrieved chunk id covers the same operator/class/page as
    the labelled expectation.

    ``corpus_*`` variants are the imported-corpus twin of a curated seed, and
    ``wiki_*`` chunks are the official wiki page (any section) of that same
    subject — a retrieved wiki section chunk of Blur TOP satisfies an
    expectation labelled ``blur_top``. An expectation given as a list is an
    OR-group: any alternative satisfies it.
    """
    if isinstance(exp_id, (list, tuple)):
        return any(_same_doc(rid, e) for e in exp_id)
    if rid == exp_id:
        return True
    if rid in ("corpus_" + exp_id, "corpus_py_" + exp_id):
        return True
    wiki_base = "wiki_" + exp_id
    return rid == wiki_base or rid.startswith(wiki_base + "__")


def _metrics(retrieved_ids, exp, must_not, k):
    # Each expectation is credited to at most one retrieved chunk. A wiki page
    # contributes many section chunks that all satisfy the same expectation;
    # counting each of them as a separate relevant result would inflate nDCG
    # above 1.0 and hide real ranking regressions.
    rel = [0.0] * len(retrieved_ids[:k])
    matched = set()              # indices into exp (entries may be OR-lists)
    for i, rid in enumerate(retrieved_ids[:k]):
        for j, e in enumerate(exp):
            if j not in matched and _same_doc(rid, e):
                matched.add(j)
                rel[i] = 1.0
                break
    rec = (len(matched) / len(exp)) if exp else 1.0
    rank = next((i + 1 for i, r in enumerate(rel) if r), None)
    mrr = (1.0 / rank) if rank else 0.0
    idcg = _dcg([1.0] * min(len(exp), k)) if exp else 1.0
    ndcg = _dcg(rel) / idcg if idcg > 0 else 0.0
    viol = [rid for rid in retrieved_ids[:k]
            if any(_same_doc(rid, m) for m in (must_not or []))]
    return rec, mrr, ndcg, viol


def applicable_queries(retriever, queries=None):
    """Drop labelled queries whose source is absent from the built KB.

    The wiki-coverage queries only have an answer when ``build_kb`` found a
    local wiki mirror; on a fresh clone or in CI they would fail for a reason
    that says nothing about retrieval quality.
    """
    queries = QUERIES if queries is None else queries
    chunks = getattr(retriever, "index", retriever).chunks
    have = {c.get("source") for c in chunks}
    return [q for q in queries if not q.get("needs") or q["needs"] in have]


def run(retriever, queries, ks=(1, 3, 5)):
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
    ap.add_argument("--name-integrity", action="store_true",
                    help="also verify every operator name resolves + no retokenization")
    args = ap.parse_args()

    ret = build_retriever()
    pr = ParallelRetriever(ret)
    queries = applicable_queries(ret)
    skipped = len(QUERIES) - len(queries)
    print(f"KB chunks: {len(ret.chunks)}  queries: {len(queries)}"
          + (f"  ({skipped} skipped — source not in KB)" if skipped else ""))
    run(pr, queries, ks=(1, 3, args.k))
    if args.name_integrity:
        report = name_integrity(ret)
        print(f"name-integrity: operators={report['operators']} "
              f"resolved={report['resolved']} unresolved={report['unresolved']} "
              f"retokenized={report['retokenized']}")
        for bad in report["unresolved_names"] + report["retokenized_names"]:
            print("  MISS:", bad)
        if report["unresolved"] or report["retokenized"]:
            raise SystemExit(1)


def name_integrity(retriever, corpus=None):
    """TrueFiasco name-integrity gate, MIT-safe reimpl.

    Every *expected chunk id* in the eval set must resolve to a real chunk in
    the retriever, and every operator-family expected id must exist in the
    corpus. This is the discipline that matters: if the eval set references
    operators/chunks that don't exist, the eval itself is unsound.

    Also reports per-family coverage across the corpus.

    Returns a report dict with ``ok`` True only when unresolved == 0 and
    retokenized == 0.
    """
    from td_mcp.kb import corpus as corpus_mod
    ops = corpus_mod.load_operators() if corpus is None else corpus
    chunk_ids = {c.get("id") for c in retriever.chunks}
    # corpus_* / corpus_py_* variants in eval satisfy the same expected hit, so
    # they don't need to exist verbatim as long as the base id does.
    unresolved_ids, retokenized_ids = [], []

    def _base(exp_id):
        return (exp_id
                .removeprefix("corpus_py_")
                .removeprefix("corpus_"))

    def _resolves(exp_id):
        # An OR-group resolves when any alternative does.
        if isinstance(exp_id, (list, tuple)):
            return any(_resolves(e) for e in exp_id)
        base = _base(exp_id)
        # Curated chunks use snake_case ids; corpus chunks are corpus_<key>;
        # wiki pages are wiki_<key> with per-section suffixes.
        if base in chunk_ids or f"corpus_{base}" in chunk_ids or f"corpus_py_{base}" in chunk_ids:
            return True
        wiki_base = f"wiki_{base}"
        return any(cid == wiki_base or cid.startswith(wiki_base + "__")
                   for cid in chunk_ids)

    for q in applicable_queries(retriever):
        for exp in q.get("exp", []):
            if not _resolves(exp):
                unresolved_ids.append(exp)

    by_family = {}
    for rec in ops.values():
        fam = rec.get("category")
        if fam:
            by_family[fam] = by_family.get(fam, 0) + 1
    report = {
        "ok": not unresolved_ids and not retokenized_ids,
        "operators": len(ops),
        "resolved": len(chunk_ids),
        "unresolved": len(unresolved_ids),
        "retokenized": len(retokenized_ids),
        "unresolved_names": unresolved_ids[:20],
        "retokenized_names": retokenized_ids[:20],
        "by_family": by_family,
    }
    return report


if __name__ == "__main__":
    main()
