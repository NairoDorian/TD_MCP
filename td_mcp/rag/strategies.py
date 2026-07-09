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

from td_mcp.rag.retriever import tokenize, version_tuple

LOCAL_PREFIX = "local:"
REMOTE_PREFIX = "remote:"


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

    def name(self):
        return f"Remote[{self.command[0]}]"

    def candidates(self, qt, query, query_vec, k):
        try:
            return self._query(query, k)
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
        keys = []
        for content in getattr(result, "content", []) or []:
            if getattr(content, "type", None) == "text":
                text = content.text
                key = REMOTE_PREFIX + str(abs(hash(text)) % 10 ** 8)
                self.remote_docs[key] = {
                    "id": key,
                    "source": "remote:" + self.tool,
                    "title": text.split("\n", 1)[0][:120],
                    "text": text,
                }
                keys.append(key)
        return keys


class ParallelRetriever:
    def __init__(self, retriever, enable_dense=True, enable_hyde=True,
                 remote=None, remote_tool="search_touchdesigner_docs",
                 remote_arg="query", reranker=None, rrf_k=60, top_m=15):
        self.retriever = retriever
        self.index = retriever.index
        self.reranker = reranker
        self.rrf_k = rrf_k
        self.top_m = top_m

        self.strategies = [BM25Strategy(self.index), TitleStrategy(self.index)]
        sources = sorted({c.get("category") for c in self.index.chunks if c.get("category")})
        for src in sources:
            self.strategies.append(SourceBM25Strategy(self.index, src))
        if enable_dense and self.index.encode and self.index.vectors:
            self.strategies.append(DenseStrategy(self.index))
            if enable_hyde:
                self.strategies.append(HyDEStrategy(self.index))

        self.remote = None
        if remote:
            try:
                self.remote = RemoteMCPStrategy(remote, remote_tool, remote_arg)
                self.strategies.append(self.remote)
            except Exception:  # noqa: BLE001
                self.remote = None
        self._idmap = None

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

        ranked = [[] for _ in self.strategies]
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.strategies)) as ex:
            futs = [ex.submit(s.candidates, qt, query, query_vec, self.rrf_k)
                   for s in self.strategies]
            for i, f in enumerate(futs):
                ranked[i] = f.result()

        rrf = collections.Counter()
        for keys in ranked:
            for rank, key in enumerate(keys):
                rrf[key] += 1.0 / (self.rrf_k + rank + 1)

        fused = sorted(rrf.keys(), key=lambda key: -rrf[key])
        out = []
        for key in fused:
            if key.startswith(LOCAL_PREFIX):
                c = self._chunk_by_id(key[len(LOCAL_PREFIX):])
                if c is None or not self._pass(c, family, category, version, source):
                    continue
                out.append((c, round(rrf[key], 4)))
            else:
                doc = self.remote.remote_docs.get(key) if self.remote else None
                if doc is None:
                    continue
                if source and not doc.get("source", "").startswith(source):
                    continue
                out.append((doc, round(rrf[key], 4)))

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
        return out[:k]
