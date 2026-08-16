"""Knowledge graph over the operator / Python corpus (Graph RAG, TrueFiasco idea).

Builds a `networkx` graph:
  * operator -> operator   ("related", from relatedOperators)
  * operator -> python-class (the operator's `*_Class` API)
  * operator -> python-class (member exposure, from python-api-compat)

Powers `related_operators`, `suggest_chain` and the offline
`td_docs_workflow` enrichment without an LLM. Pure stdlib + networkx.
"""

from typing import Dict, List

from td_mcp.kb import corpus


def build_graph():
    import networkx as nx

    g = nx.Graph()
    ops = corpus.load_operators()
    classes = corpus.load_python_api()
    for key, rec in ops.items():
        g.add_node(key, kind="operator", family=rec.get("category"))
        for rel in rec.get("relatedOperators") or []:
            rt = (rel.get("id") if isinstance(rel, dict) else rel)
            if rt in ops and rt != key:
                g.add_edge(key, rt, relation="related")
        cls_name = rec.get("className") or f"{rec.get('name','').replace(' ', '')}_Class"
        if cls_name in classes:
            g.add_node(cls_name, kind="python")
            g.add_edge(key, cls_name, relation="api")
    return g


_GRAPH = None


def graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


def related_operators(name, depth=1):
    g = graph()
    rec = corpus.operator_record(name)
    if rec is None:
        return []
    key = rec.get("id")
    if key not in g:
        return []
    seen = {key}
    frontier = {key}
    for _ in range(depth):
        nxt = set()
        for n in frontier:
            for nb in g.neighbors(n):
                if nb not in seen:
                    seen.add(nb)
                    nxt.add(nb)
        frontier = nxt
    return [n for n in seen if n != key and g.nodes[n].get("kind") == "operator"]


def suggest_chain(query, k=6):
    """Walk the graph from the top keyword-matched operators to assemble a
    likely operator chain (used by td_docs_workflow)."""
    seeds = corpus.suggest_workflow(query, k=k)
    g = graph()
    chain = []
    seen = set()
    for s in seeds:
        rec = corpus.operator_record(s["name"])
        if not rec:
            continue
        key = rec.get("id")
        if key in seen or key not in g:
            continue
        seen.add(key)
        chain.append(s["name"])
        for nb in g.neighbors(key):
            if g.nodes[nb].get("kind") == "operator" and nb not in seen:
                seen.add(nb)
                r = corpus.operator_record(nb)
                if r:
                    chain.append(r.get("displayName") or r.get("name"))
    return chain


def _kn(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "").replace("_", "")


def combo_related(name: str, k: int = 8) -> List[str]:
    """Operators that *work together* with ``name`` (relationship expansion).

    Builds co-occurrence from each operator's ``workflowPatterns`` text and from
    ``name``'s own patterns — i.e. "show me real networks that use Noise with X".
    Pure corpus scan, no LLM. Returns display names ranked by co-occurrence.
    """
    rec = corpus.operator_record(name)
    if rec is None:
        return []
    key = rec.get("id")

    ops = corpus.load_operators()
    name_to_id: Dict[str, str] = {}
    for kk, rr in ops.items():
        dn = rr.get("displayName") or rr.get("name") or ""
        nm = rr.get("name") or ""
        if dn:
            name_to_id[_kn(dn)] = kk
        if nm:
            name_to_id[_kn(nm)] = kk

    target_norm = _kn(rec.get("displayName") or rec.get("name"))
    scores: Dict[str, int] = {}

    # Others whose workflowPatterns mention the target.
    for kk, rr in ops.items():
        if kk == key:
            continue
        blob = _kn(" ".join(str(p) for p in (rr.get("workflowPatterns") or [])))
        if target_norm and target_norm in blob:
            scores[kk] = scores.get(kk, 0) + 1

    # Target's own patterns mentioning others.
    for p in (rec.get("workflowPatterns") or []):
        blob = _kn(str(p))
        for nn, iid in name_to_id.items():
            if iid != key and nn and nn in blob:
                scores[iid] = scores.get(iid, 0) + 1

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:k]
    out = []
    for iid, _ in ranked:
        r = ops.get(iid)
        if r:
            out.append(r.get("displayName") or r.get("name"))
    return out


def family_disambiguation(name):
    """Return a disambiguation warning when ``name`` is ambiguous across families.

    TouchDesigner operator families share names: "Noise" resolves to Noise TOP,
    Noise CHOP, Noise SOP, Noise POP — four distinct operators. When a query
    matches more than one family, the agent should be told to disambiguate
    rather than silently receiving one random pick.

    Returns a warning string, or ``None`` when the name is unambiguous / not
    found.
    """
    recs = corpus.operators_matching(name)
    if not recs:
        return None
    families = sorted({r.get("category") for r in recs if r.get("category")})
    if len(families) > 1:
        detail = ", ".join(
            f"{r.get('displayName') or r.get('name')} ({r.get('category')})"
            for r in recs
        )
        return (
            f"Ambiguous name {name!r}: matches {len(families)} families "
            f"[{', '.join(families)}]. Specify the full name, e.g. "
            f"{recs[0].get('displayName') or recs[0].get('name')}. "
            f"All matches: {detail}."
        )
    return None


FAMILIES = ["TOP", "CHOP", "SOP", "DAT", "MAT", "COMP", "POP"]


def sibling_operators(title):
    """Same-named operators in other families (wiki-rag disambiguation guardrail).

    "Noise TOP" → ["Noise CHOP", "Noise SOP", "Noise POP"]. A search that
    matched one family must know the same concept exists elsewhere before
    treating the answer as THE answer.
    """
    parts = (title or "").rsplit(" ", 1)
    if len(parts) != 2 or parts[1].upper() not in FAMILIES:
        return []
    base = parts[0]
    recs = corpus.operators_matching(base)
    out = []
    for r in recs:
        fam = (r.get("category") or "").upper()
        if fam and fam != parts[1].upper():
            dn = r.get("displayName") or r.get("name") or ""
            if dn:
                out.append(dn)
    return sorted(set(out))
