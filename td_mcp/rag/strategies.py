"""Parallel multi-RAG: several retrieval backends run concurrently and are
fused with Reciprocal Rank Fusion (RRF).

Two kinds of backend, in one fusion space keyed by document URI:
  * LOCAL  (prefix "local:")  - our Index strategies:
        global BM25, per-source BM25 (operators/python/glsl/tutorials
        as separate KBs), global MiniLM dense, HyDE, title boost.
  * REMOTE (prefix "remote:") - an EXTERNAL MCP doc-RAG server
        (cacheflowe/td-docs-mcp, bottobot, ...) launched over stdio
        and queried live. This is the "combine multiple RAG in
        parallel" leap: two independent RAG systems, one fused answer.

They run in a thread pool (the slow encoder overlaps lexical work
and the remote stdio round-trip), RRF merges, an optional
CrossEncoder reranks the top-M.

Fusion is over document URIs, not our indices, so a remote doc
that doesn't exist locally still contributes its rank.
"""

import collections
import concurrent.futures
import os
import sqlite3

from td_mcp.rag.retriever import tokenize, version_tuple

LOCAL_PREFIX = "local:"
REMOTE_PREFIX = "remote:"
WIKI_PREFIX = "wiki:"


class Strategy:
    def name(self):
        return self.__class__.__name__

    def candidates(self, qt, query, query_vec, k):
        raise NotImplementedError


class BM25Strategy(Strategy):
    def __init__(self, index):
        self.index = index

    def candidates(self, qt, query, query_vec, k):
        scores = self.index.bm25(qt)
        order = sorted(range(self.index.N), key=lambda i: -scores[i])
        return [LOCAL_PREFIX + self.index.chunks[i]["id"] for i in order[:k]]


class DenseStrategy(Strategy):
    def __init__(self, index):
        self.index = index

    def candidates(self, qt, query, query_vec, k):
        if not (self.index.encode and self.index.vectors):
            return []
        scores = self.index.dense(query, query_vec=query_vec)
        order = sorted(range(self.index.N), key=lambda i: -scores[i])
        return [LOCAL_PREFIX + self.index.chunks[i]["id"] for i in order[:k]]


class HyDEStrategy(Strategy):
    """Always-on HyDE kept for explicit opt-in; the adaptive variant below is
    the default behavior when dense embeddings are present."""
    def __init__(self, index):
        self.index = index

    def candidates(self, qt, query, query_vec, k):
        if not (self.index.encode and self.index.vectors) or query_vec is None:
            return []
        hyp = "A TouchDesigner documentation page describing: " + query
        qv = self.index.encode([hyp])[0]
        scores = self.index.dense(hyp, query_vec=qv)
        order = sorted(range(self.index.N), key=lambda i: -scores[i])
        return [LOCAL_PREFIX + self.index.chunks[i]["id"] for i in order[:k]]


class AdaptiveHyDEStrategy(HyDEStrategy):
    """HyDE that only fires when the fused top score says the base pass likely
    missed (wiki-rag finding: always-on HyDE is a net wash, low-score-triggered
    HyDE is a win). The keep-only-if-better decision is made in
    ``ParallelRetriever.search`` because it must compare two full fused passes.

    ``candidates`` here is the same single-shot hypothetical-document pass;
    the adaptive trigger lives in ``search``.
    """
    def name(self):
        return "HyDE[adaptive]"


class TitleStrategy(Strategy):
    def __init__(self, index, w=0.3):
        self.index = index
        self.w = w

    def candidates(self, qt, query, query_vec, k):
        s = set(qt)
        scores = [self.w * (len(s & self.index.title_tokens[i]) / max(len(s), 1))
                  for i in range(self.index.N)]
        order = sorted(range(self.index.N), key=lambda i: -scores[i])
        return [LOCAL_PREFIX + self.index.chunks[i]["id"] for i in order[:k]]


class SourceBM25Strategy(Strategy):
    def __init__(self, index, source):
        self.index = index
        self.source = source
        self.idx = [i for i, c in enumerate(index.chunks)
                  if c.get("category") == source]

    def name(self):
        return f"BM25[{self.source}]"

    def candidates(self, qt, query, query_vec, k):
        if not self.idx:
            return []
        scores = self.index.bm25(qt)
        sub = sorted(self.idx, key=lambda i: -scores[i])
        return [LOCAL_PREFIX + self.index.chunks[i]["id"] for i in sub[:k]]


class RemoteMCPStrategy(Strategy):
    """Queries an EXTERNAL MCP doc-RAG server over stdio and fuses its
    results. Each remote hit becomes a REMOTE_PREFIX document URI."""

    def __init__(self, command, tool="search_touchdesigner_docs", arg="query"):
        self.command = command  # e.g. ["uv", "run", "td-docs-mcp"]
        self.tool = tool
        self.arg = arg
        self.remote_docs = {}
        self._cache = {}

    def name(self):
        return f"Remote[{self.command[0]}]"

    def candidates(self, qt, query, query_vec, k):
        try:
            if query in self._cache:
                return self._cache[query]
            result = self._query(query, k)
            self._cache[query] = result
            return result
        except Exception:  # noqa: BLE001
            return []

    def _query(self, query, k):
        import asyncio

        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        async def run():
            params = StdioServerParameters(
                command=self.command[0], args=self.command[1:])
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as sess:
                    await sess.initialize()
                    res = await sess.call_tool(self.tool, {self.arg: query})
                    return self._parse(res)

        return asyncio.run(run())

    def _parse(self, result):
        import hashlib

        keys = []
        for content in getattr(result, "content", []) or []:
            if getattr(content, "type", None) == "text":
                text = content.text
                key = REMOTE_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
                self.remote_docs[key] = {
                    "id": key,
                    "source": "remote:" + self.tool,
                    "title": text.split("\n", 1)[0][:120],
                    "text": text,
                }
                keys.append(key)
        return keys


class SqliteWikiStrategy(Strategy):
    """Consume a touchdesigner-wiki-rag SQLite FTS5 index (manual.db).

    Brings wiki-wide coverage — tutorials, concept pages, and full operator
    docs not in our curated KB — into the RRF fusion pool.  Opt-in via the
    ``wiki_db`` arg or ``TD_MCP_WIKI_DB`` env var (path to a wiki-rag
    ``data/manual.db``).  Uses FTS5 BM25 only — no chromadb or
    sentence-transformers dependency.

    Wiki chunk URIs use the ``wiki:`` prefix and are resolved by
    ``ParallelRetriever`` via ``self.wiki_docs`` (same pattern as
    ``RemoteMCPStrategy.remote_docs``).

    The FTS5 schema (from wiki-rag ingest.py) is:
      chunks(chunk_id, text, heading, title, family, page_type, revid)
      tokenize='porter unicode61'
    """

    def __init__(self, db_path):
        self.db_path = db_path
        self.wiki_docs: dict = {}

    def name(self):
        return "Wiki[sqlite]"

    def candidates(self, qt, query, query_vec, k):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            terms = [t for t in qt if len(t) > 1]
            if not terms:
                conn.close()
                return []
            # Same FTS5 query shape wiki-rag uses: quoted terms, OR-joined.
            match = " OR ".join('"{}"'.format(t) for t in terms)
            rows = conn.execute(
                "SELECT chunk_id, text, title, heading, anchor, family "
                "FROM chunks WHERE chunks MATCH ? "
                "ORDER BY bm25(chunks, 1.0, 2.0, 3.0) LIMIT ?",
                (match, k),
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            return []

        out = []
        for r in rows:
            cid = r["chunk_id"]
            uri = WIKI_PREFIX + cid
            title = (r["title"] or "").strip()
            anchor = (r["anchor"] or "").strip()
            url = "https://docs.derivative.ca/" + title.replace(" ", "_")
            if anchor:
                url += "#" + anchor
            family = (r["family"] or "").strip() or None
            self.wiki_docs[uri] = {
                "id": uri,
                "source": "wiki-rag",
                "title": title,
                "family": family,
                "category": "concept",
                "url": url,
                "min_version": "all",
                "text": self._format_chunk(r),
            }
            out.append(uri)
        return out

    @staticmethod
    def _format_chunk(r):
        text = (r["text"] or "").strip()
        heading = (r["heading"] or "").strip()
        if heading and heading != "(intro)":
            text = "### " + heading + "\n\n" + text
        return text

    def browse(self, query=None, family=None, limit=40):
        """L0 — section/title map over the wiki FTS5 ``sections`` index, if
        present (wiki-rag ships one). Falls back to chunk titles silently."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            terms = [t for t in tokenize(query) if len(t) > 1] if query else []
            if terms:
                match = " OR ".join('"{}"'.format(t) for t in terms)
                sql = "SELECT heading, title, family, anchor FROM sections WHERE sections MATCH ?"
                params = [match]
                if family:
                    sql += " AND family = ?"
                    params.append(family)
                sql += " ORDER BY rank LIMIT ?"
            else:
                sql = "SELECT heading, title, family, anchor FROM sections"
                params = []
                if family:
                    sql += " WHERE family = ?"
                    params.append(family)
                sql += " ORDER BY title LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            conn.close()
            keys = ("heading", "title", "family", "anchor")
            return [dict(zip(keys, r)) for r in rows]
        except sqlite3.Error:
            return []

    def stats(self):
        """KB statistics (page/chunk counts, operators per family)."""
        try:
            conn = sqlite3.connect(self.db_path)
            pages = conn.execute(
                "SELECT COUNT(*), SUM(chunks) FROM pages").fetchone()
            fam = conn.execute(
                "SELECT family, COUNT(*) FROM pages WHERE page_type='operator' "
                "GROUP BY family ORDER BY family").fetchall()
            conn.close()
            return {"pages": pages[0] if pages else 0,
                    "chunks": pages[1] if pages else 0,
                    "operators": dict(fam)}
        except sqlite3.Error:
            return {}


class ParallelRetriever:
    def __init__(self, retriever, enable_dense=True, enable_hyde=True,
                 remote=None, wiki_db=None, remote_tool="search_touchdesigner_docs",
                 remote_arg="query", reranker=None, rrf_k=60, top_m=15,
                 hyde_trigger=0.012, operator_boost=0.08, score_floor=0.0):
        self.retriever = retriever
        self.index = retriever.index
        self.reranker = reranker
        self.rrf_k = rrf_k
        self.top_m = top_m
        self.hyde_trigger = hyde_trigger
        self.operator_boost = operator_boost
        self.score_floor = score_floor

        self.strategies = [BM25Strategy(self.index), TitleStrategy(self.index)]
        sources = sorted({c.get("category") for c in self.index.chunks if c.get("category")})
        for src in sources:
            self.strategies.append(SourceBM25Strategy(self.index, src))
        self.hyde_strategy = None
        if enable_dense and self.index.encode and self.index.vectors:
            self.strategies.append(DenseStrategy(self.index))
            if enable_hyde:
                self.hyde_strategy = AdaptiveHyDEStrategy(self.index)

        self.remote = None
        if remote:
            try:
                self.remote = RemoteMCPStrategy(remote, remote_tool, remote_arg)
                self.strategies.append(self.remote)
            except Exception:  # noqa: BLE001
                self.remote = None

        # Optional wiki-rag SQLite FTS5 index (wiki-wide coverage beyond our
        # curated KB). Falls back to TD_MCP_WIKI_DB env var.
        self.wiki_docs: dict = {}
        wiki_path = wiki_db or os.environ.get("TD_MCP_WIKI_DB")
        if wiki_path and os.path.exists(wiki_path):
            try:
                wiki_strat = SqliteWikiStrategy(wiki_path)
                self.strategies.append(wiki_strat)
                self.wiki_docs = wiki_strat.wiki_docs
            except Exception:  # noqa: BLE001
                pass
        self._idmap = None
        self._op_title_map_cache = None

    def _chunk_by_id(self, cid):
        if self._idmap is None:
            self._idmap = {c["id"]: c for c in self.index.chunks}
        return self._idmap.get(cid)

    def _pass(self, c, family, category, version, source):
        if family and c.get("family") and c["family"].lower() != family.lower():
            return False
        if category and c.get("category") and c["category"] != category:
            return False
        if source and c.get("source") and c["source"] != source:
            return False
        mv = c.get("min_version") or c.get("version")
        if version and mv and mv != "all" and version_tuple(mv) > version_tuple(version):
            return False
        return True

    def search(self, query, family=None, category=None, version=None,
               source=None, k=5):
        qt = tokenize(query)
        if not qt:
            return []
        query_vec = None
        if self.index.encode:
            try:
                query_vec = self.index.encode([query])[0]
            except Exception:  # noqa: BLE001
                query_vec = None

        # Plural stemming for lexical matching (cheap, no expansion blowup):
        # BM25 + Title + Source strategies all receive the same stemmed qt.
        # Only "pops"→"pop" matters in practice for the KB eval, but handle
        # the generic case.
        qt_stemmed = qt
        if any(t.endswith("s") and t[:-1] in ("pop", "sop", "top", "chop", "dat", "mat", "comp") for t in qt):
            qt_stemmed = list(qt)
            for i, t in enumerate(qt_stemmed):
                if t.endswith("s") and t[:-1] in ("pop", "sop", "top", "chop", "dat", "mat", "comp"):
                    qt_stemmed[i] = t[:-1]

        ranked = [[] for _ in self.strategies]
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.strategies)) as ex:
            futs = [ex.submit(s.candidates, qt_stemmed, query, query_vec, self.rrf_k)
                   for s in self.strategies]
            for i, f in enumerate(futs):
                ranked[i] = f.result()

        rrf = collections.Counter()
        for keys in ranked:
            for rank, key in enumerate(keys):
                rrf[key] += 1.0 / (self.rrf_k + rank + 1)

        # Operator-aware intent boost (TrueFiasco router, MIT-safe reimpl):
        # when the query names an operator we know, hard-boost that operator's
        # chunks in the RRF space. This makes "noise top amplitude" surface the
        # Noise TOP parameter chunks over generic "noise" matches.
        # The stemmed query string is used so "pops" → "pop" matches POP ops.
        if self.operator_boost:
            stemmed_query = " ".join(qt_stemmed)
            boost_keys = self._operator_chunk_uris(stemmed_query)
            if not boost_keys:
                boost_keys = self._operator_chunk_uris(query)
            for key in boost_keys:
                rrf[key] += self.operator_boost

            # Family-overview boost: queries naming a family plus a workflow
            # word ("particle simulation with pops", "audio reactive setup")
            # should surface the family-overview/tutorial chunks too.
            family_hints = [t[:-1] if t.endswith("s") else t for t in qt
                            if t in ("pops", "sops", "tops", "chops", "dats", "mats", "comps")
                            or t[:-1] in ("pop", "sop", "top", "chop", "dat", "mat", "comp") and t.endswith("s")]
            workflow_words = ("simulation", "setup", "recipe", "workflow", "reactive", "particle")
            if family_hints and any(t in qt for t in workflow_words):
                for c in self.index.chunks:
                    title = (c.get("title") or "").lower()
                    if "overview" in title or "family overview" in title:
                        rrf[LOCAL_PREFIX + c["id"]] += self.operator_boost * 0.75

            # Tutorial-workflow boost: when the query explicitly carries an
            # "audio reactive"-style intent (not just "audio"), boost tutorial
            # chunks with export/parameter wording.
            if "audio" in qt and any(t in qt for t in ("reactive", "setup")):
                for c in self.index.chunks:
                    if c.get("category") != "tutorial":
                        continue
                    text = (c.get("text") or "").lower()
                    if "export" in text and ("chop" in text or "parameter" in text):
                        rrf[LOCAL_PREFIX + c["id"]] += self.operator_boost * 0.5

        out = self._fuse(rrf, family, category, version, source, k)

        # Adaptive HyDE (wiki-rag): run the hypothetical-doc pass only when the
        # base fused pass looks like a miss, and keep the alternative only if
        # its top score is strictly better.
        if self.hyde_strategy is not None and (not out or out[0][1] < self.hyde_trigger):
            try:
                alt_keys = self.hyde_strategy.candidates(qt, query, query_vec, self.rrf_k)
                if alt_keys:
                    alt_rrf = rrf.copy()
                    for rank, key in enumerate(alt_keys):
                        alt_rrf[key] += 1.0 / (self.rrf_k + rank + 1)
                    alt_out = self._fuse(alt_rrf, family, category, version, source, k)
                    if alt_out and (not out or alt_out[0][1] > out[0][1]):
                        out = alt_out
            except Exception:  # noqa: BLE001
                pass

        if self.reranker and out:
            top = out[:self.top_m]
            reranked = self.reranker.rerank(query, [d for d, _ in top])
            # Carry the original RRF score with each doc by id so the
            # reranker's new ordering keeps the correct score attached
            # (pairing by position would shuffle scores onto wrong docs).
            score_by_id = {c["id"]: sc for c, sc in top}
            reranked_scored = [(d, score_by_id.get(d["id"])) for d in reranked]
            rest = out[self.top_m:]
            out = reranked_scored + rest

        # Score-floor abstention (TrueFiasco, MIT-safe): when every fused score
        # sits below the floor, treat the query as out-of-domain and return
        # nothing instead of surfacing low-confidence garbage.
        if self.score_floor and out and out[0][1] < self.score_floor:
            return []
        return out[:k]

    def _operator_chunk_uris(self, query):
        """Return local chunk URIs for any operator named in ``query``.

        Uses the corpus operator lookup (alias-tolerant) and returns
        ``local:<chunk_id>`` for every chunk whose title/family matches the
        resolved operator. Empty list when no operator is detected.
        """
        try:
            from td_mcp.kb import corpus
            ops = corpus.load_operators()
            ql = query.lower().strip()
            # Nickname path: a 1-2 word query that exactly equals a curated
            # chunk's alias ("movie" -> Movie File In TOP) boosts that chunk —
            # exact-alias-only so short queries don't spray boosts.
            if len(ql.split()) <= 2:
                keys = []
                for c in self.index.chunks:
                    if ql in [a.lower() for a in (c.get("aliases") or [])]:
                        uri = LOCAL_PREFIX + c["id"]
                        if uri not in keys:
                            keys.append(uri)
                if keys:
                    return keys
            if self._op_title_map_cache is None:
                m = {}
                for c in self.index.chunks:
                    if c.get("category") != "operator":
                        continue
                    title = (c.get("title") or "").lower()
                    if title:
                        m.setdefault(title, []).append(LOCAL_PREFIX + c["id"])
                self._op_title_map_cache = m
            keys = []
            # Only boost when the FULL operator name appears in the query —
            # single-token probes like "top" would match 100+ TOP operators.
            for key, rec in ops.items():
                name = (rec.get("displayName") or rec.get("name") or "").lower()
                if not name:
                    continue
                if name in ql or key.lower() in ql:
                    uris = self._op_title_map_cache.get(name, [])
                    for uri in uris:
                        if uri not in keys:
                            keys.append(uri)
                    continue
                # Aliases with 2+ words are precise enough to boost too.
                for alias in rec.get("aliases") or []:
                    al = alias.lower()
                    if len(al.split()) >= 2 and al in ql:
                        uris = self._op_title_map_cache.get(name, [])
                        for uri in uris:
                            if uri not in keys:
                                keys.append(uri)
                        break
            return keys
        except Exception:  # noqa: BLE001
            return []

    def _fuse(self, rrf, family, category, version, source, k):
        """Resolve fused RRF document URIs into (chunk, score) results."""
        fused = sorted(rrf.keys(), key=lambda key: -rrf[key])
        out = []
        for key in fused:
            if key.startswith(LOCAL_PREFIX):
                c = self._chunk_by_id(key[len(LOCAL_PREFIX):])
                if c is None or not self._pass(c, family, category, version, source):
                    continue
                out.append((c, round(rrf[key], 4)))
            elif key.startswith(WIKI_PREFIX):
                doc = self.wiki_docs.get(key)
                if doc is None:
                    continue
                if family and doc.get("family") and doc["family"].lower() != family.lower():
                    continue
                if source and not doc.get("source", "").startswith(source):
                    continue
                out.append((doc, round(rrf[key], 4)))
            else:
                doc = self.remote.remote_docs.get(key) if self.remote else None
                if doc is None:
                    continue
                if source and not doc.get("source", "").startswith(source):
                    continue
                out.append((doc, round(rrf[key], 4)))
        return out[:k]
