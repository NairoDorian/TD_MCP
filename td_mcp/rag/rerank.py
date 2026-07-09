"""Optional late-stage reranker (CrossEncoder) for the fused candidate list.

The parallel RAG already fused rankings with RRF; a cross-encoder
rernanks the top-M by scoring (query, document) pairs jointly,
which is more accurate than lexical/dense similarity alone.
Opt-in: import fails gracefully if sentence_transformers is absent.
"""

from td_mcp.rag.retriever import tokenize


class CrossEncoderReranker:
    def __init__(self, model_name="cross-encoder/ms-marco-MiniLM-L-6-v2", top_m=15):
        from sentence_transformers import CrossEncoder
        self.model = CrossEncoder(model_name)
        self.top_m = top_m

    def name(self):
        return "CrossEncoder"

    def rerank(self, query, chunks):
        if not chunks:
            return []
        pairs = [(query, c.get("text", "")) for c in chunks[:self.top_m]]
        scores = self.model.predict(pairs)
        order = sorted(range(len(pairs)), key=lambda i: -float(scores[i]))
        return [chunks[i] for i in order] + chunks[self.top_m:]
