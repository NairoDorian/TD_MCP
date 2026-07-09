"""Build a (optionally dense) index over the KB chunks.

Pure-stdlib path (default): validates chunks.jsonl and reports stats.
Dense path:  TD_MCP_DENSE=1 uv run ...
  - encodes every chunk with all-MiniLM-L6-v2 (sentence_transformers)
  - writes kb/embeddings.jsonl  {id, vec:[...]}  (consumed by
    retriever.Index for the live dense scorer)

The offline retriever already does hybrid (BM25 + TF-IDF cosine) with
zero deps; the dense path upgrades the 'dense' half and lifts
semantic recall (the TrueFiasco 0.86 -> 0.93 effect).
"""

import json
import os

HERE = os.path.dirname(__file__)
CHUNKS = os.path.join(HERE, "chunks.jsonl")
EMBED = os.path.join(HERE, "embeddings.jsonl")


def load():
    with open(CHUNKS, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def stats(chunks):
    by_fam, by_cat, by_src = {}, {}, {}
    for c in chunks:
        by_fam[c.get("family") or "none"] = by_fam.get(c.get("family") or "none", 0) + 1
        by_cat[c.get("category") or "none"] = by_cat.get(c.get("category") or "none", 0) + 1
        by_src[c.get("source") or "none"] = by_src.get(c.get("source") or "none", 0) + 1
    return len(chunks), by_fam, by_cat, by_src


def build_dense(chunks, model_name="all-MiniLM-L6-v2"):
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(model_name)
    texts = [f"{c.get('title','')}\n{c.get('text','')}" for c in chunks]
    vecs = model.encode(texts, show_progress_bar=False)
    with open(EMBED, "w", encoding="utf-8") as fh:
        for c, v in zip(chunks, vecs):
            fh.write(json.dumps({"id": c["id"], "vec": [float(x) for x in v]}) + "\n")
    return len(vecs)


def main():
    chunks = load()
    n, fam, cat, src = stats(chunks)
    print(f"chunks: {n}")
    print(f"by family:  {fam}")
    print(f"by category: {cat}")
    print(f"by source:   {src}")
    if os.environ.get("TD_MCP_DENSE") == "1":
        print("building dense embeddings (all-MiniLM-L6-v2)...")
        print(f"embeddings: {build_dense(chunks)} -> {EMBED}")
    else:
        print("skip dense: set TD_MCP_DENSE=1 to add MiniLM embeddings")


if __name__ == "__main__":
    main()
