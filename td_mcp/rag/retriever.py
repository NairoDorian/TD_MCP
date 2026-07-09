"""Hybrid retrieval over a local TouchDesigner knowledge base.

Two scorers, fused:
  * BM25  (lexical)        - exact param/operator-name matches
  * Dense (MiniLM vectors) - semantic paraphrase matches (optional; needs
    sentence-transformers + embeddings.jsonl, else falls back to TF-IDF cosine)

Plus a title-overlap boost and a version resolver (POPs need build>=2023.10000).

Pure standard library is enough for BM25 + TF-IDF. Real vectors are
opt-in so the package runs with zero install.
"""

import json
import math
import os
import re
from collections import Counter

TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")

DEFAULT_CHUNKS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "kb", "chunks.jsonl")
DEFAULT_EMBED = os.path.join(os.path.dirname(os.path.dirname(__file__)), "kb", "embeddings.jsonl")


def tokenize(text):
    return TOKEN_RE.findall((text or "").lower())


def version_tuple(v):
    if not v or v == "all":
        return (0,)
    parts = re.findall(r"\d+", str(v))
    return tuple(int(p) for p in parts[:4]) + (0,) * (4 - min(4, len(parts)))


def load_chunks(path):
    chunks = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def load_embeddings(path):
    vecs = {}
    if not os.path.exists(path):
        return vecs
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            vecs[row["id"]] = row["vec"]
    return vecs


class Index:
    def __init__(self, chunks, encode=None, vectors=None, title_w=0.3):
        self.chunks = chunks
        self.encode = encode
        self.vectors = vectors or {}
        self.title_w = title_w
        self.doc_tokens = [tokenize(c.get("text", "")) for c in chunks]
        self.df = Counter()
        for toks in self.doc_tokens:
            for t in set(toks):
                self.df[t] += 1
        self.N = max(len(self.doc_tokens), 1)
        self.avgdl = sum(len(t) for t in self.doc_tokens) / max(self.N, 1)
        self.k1 = 1.5
        self.b = 0.75
        self.idf = {
            t: math.log((self.N - c + 0.5) / (c + 0.5) + 1)
            for t, c in self.df.items()
        }
        self.doc_tf = [Counter(toks) for toks in self.doc_tokens]
        self.doc_len = [len(t) for t in self.doc_tokens]
        # Title + aliases are strong relevance signal and the most
        # reliable "nickname" recall path (e.g. "movie" -> Movie File In TOP).
        self.title_tokens = []
        for c in chunks:
            toks = set(tokenize(c.get("title", "")))
            for a in c.get("aliases", []) or []:
                toks |= set(tokenize(a))
            self.title_tokens.append(toks)

    def bm25(self, qt):
        scores = [0.0] * self.N
        for q in set(qt):
            idf = self.idf.get(q, 0.0)
            for i in range(self.N):
                f = self.doc_tf[i].get(q, 0)
                if f == 0:
                    continue
                dl = self.doc_len[i]
                denom = f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
                scores[i] += idf * denom
        return scores

    def tfidf_dense(self, qt):
        qtf = Counter(qt)
        qvec = {t: self.idf.get(t, 0.0) * c for t, c in qtf.items()}
        scores = [0.0] * self.N
        for i in range(self.N):
            dot = 0.0
            norm = 0.0
            for t, w in qvec.items():
                dtf = self.doc_tf[i].get(t, 0)
                if dtf:
                    dw = self.idf.get(t, 0.0) * dtf
                    dot += w * dw
                    norm += dw * dw
            scores[i] = dot / (math.sqrt(norm) or 1.0)
        return scores

    def dense(self, query, query_vec=None):
        if not (self.encode and self.vectors):
            return self.tfidf_dense(tokenize(query))
        qv = query_vec if query_vec is not None else self.encode([query])[0]
        scores = [0.0] * self.N
        for i, c in enumerate(self.chunks):
            v = self.vectors.get(c["id"])
            if v is None:
                continue
            denom = math.sqrt(sum(a * a for a in v)) or 1.0
            num = sum(a * b for a, b in zip(qv, v))
            scores[i] = num / denom
        return scores

    def title_scores(self, qt):
        s = set(qt)
        return [self.title_w * (len(s & self.title_tokens[i]) / max(len(s), 1))
                for i in range(self.N)]

    def rank(self, scores, k):
        order = sorted(range(self.N), key=lambda i: -scores[i])
        return order[:k]


class Retriever:
    def __init__(self, chunks, encode=None, vectors=None):
        self.index = Index(chunks, encode=encode, vectors=vectors)
        self.chunks = chunks

    def search(self, query, family=None, category=None, version=None,
               source=None, k=5, bm25_w=0.5, dense_w=0.5):
        qt = tokenize(query)
        if not qt:
            return []
        bs = self._norm(self.index.bm25(qt))
        ds = self._norm(self.index.dense(query))
        fused = [bs[i] * bm25_w + ds[i] * dense_w for i in range(self.index.N)]
        for i, ts in enumerate(self.index.title_scores(qt)):
            fused[i] += ts
        return self._finalize(fused, query, family, category, version, source, k)

    def _finalize(self, fused, query, family, category, version, source, k):
        qt = set(tokenize(query))
        vt = version_tuple(version) if version else None
        out = []
        for i, sc in enumerate(fused):
            c = self.chunks[i]
            if family and c.get("family") and c["family"].lower() != family.lower():
                continue
            if category and c.get("category") and c["category"] != category:
                continue
            if source and c.get("source") and c["source"] != source:
                continue
            mv = c.get("min_version") or c.get("version")
            if vt and mv and mv != "all" and version_tuple(mv) > vt:
                continue
            out.append((c, round(sc, 4)))
        out.sort(key=lambda x: -x[1])
        return out[:k]

    @staticmethod
    def _norm(vec):
        mx = max(vec) if vec else 0.0
        return vec if mx <= 0 else [x / mx for x in vec]

    # --- catalog / discovery helpers -------------------------------------
    def families(self):
        """Distinct operator families present in the KB (TOP, CHOP, ...)."""
        fams = []
        for c in self.chunks:
            f = c.get("family")
            if f and f not in fams:
                fams.append(f)
        return fams

    def operators_in(self, family, version=None):
        """All operator chunks of a family, version-filtered."""
        vt = version_tuple(version) if version else None
        out = []
        for c in self.chunks:
            if c.get("family") != family:
                continue
            mv = c.get("min_version")
            if vt and mv and mv != "all" and version_tuple(mv) > vt:
                continue
            out.append(c)
        return out

    def glossary(self, limit=200):
        """Compact title list of every chunk for exploration / autocomplete."""
        seen = set()
        rows = []
        for c in self.chunks:
            key = (c.get("family") or c.get("category"), c.get("title"))
            if key in seen:
                continue
            seen.add(key)
            rows.append((c.get("title"), c.get("family") or c.get("category"),
                         c.get("min_version")))
            if len(rows) >= limit:
                break
        return rows

    def parameter_doc(self, op_name):
        """Best doc chunk for an operator's parameters (category=operator)."""
        res = self.search(op_name, category="operator", k=1)
        return res[0][0] if res else None


def build_retriever(path=DEFAULT_CHUNKS):
    chunks = load_chunks(path)
    encode = None
    vectors = None
    if os.environ.get("TD_MCP_DENSE") == "1":
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("all-MiniLM-L6-v2")
            encode = lambda texts: model.encode(texts, show_progress_bar=False)
            vectors = load_embeddings(DEFAULT_EMBED)
        except Exception:  # noqa: BLE001
            encode = None
            vectors = None
    return Retriever(chunks, encode=encode, vectors=vectors)


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) or "blur top parameters"
    for c, sc in build_retriever().search(q, k=3):
        print(f"[{sc}] {c.get('title')} ({c.get('family') or c.get('category')})")
        print("   ", c.get("text", "")[:140].replace("\n", " "))
