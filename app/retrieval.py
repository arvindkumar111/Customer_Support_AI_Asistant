"""Hybrid retrieval: dense (pgvector) + keyword (Postgres full-text) fused with
Reciprocal Rank Fusion (RRF), then re-ranked by a cross-encoder.

Quick manual test:
    python -m app.retrieval "Mera laptop damaged aaya hai, kitne din mein return kar sakta hu?"
    python -m app.retrieval "warranty on phones" --category warranty
"""
import re
import sys
from dataclasses import dataclass

import numpy as np

from app import config
from app.db import get_conn

# Filler words that would match almost every chunk on the keyword side (no IDF in Postgres ts_rank).
STOPWORDS = {
    "the", "is", "are", "a", "an", "of", "to", "for", "in", "on", "my", "me", "i", "you", "your", "do", "does",
    "what", "can", "how", "and", "or", "it", "this", "that", "with", "be", "have", "has",
    "hai", "hain", "ka", "ki", "ke", "ko", "se", "mein", "me", "mera", "meri", "mere", "kya", "kab", "kitne",
    "kitna", "kar", "sakta", "sakte", "hu", "hun", "ho", "aur", "bhi", "par", "pe", "to", "toh", "nahi",
    # Devanagari fillers
    "का", "की", "के", "को", "में", "से", "है", "हैं", "और", "कि", "पर", "कितने", "कितना", "क्या", "होती", "होता", "मेरा", "मेरी",
}

HYBRID_SQL = """
WITH
dense AS (
    SELECT id, row_number() OVER (ORDER BY d) AS rnk
    FROM (
        SELECT id, embedding <=> %(qvec)s AS d
        FROM kb.chunks
        WHERE (%(cats)s::text[] IS NULL OR category = ANY(%(cats)s::text[]))
          AND (%(pls)s::text[] IS NULL OR product_line = ANY(%(pls)s::text[]))
        ORDER BY embedding <=> %(qvec)s
        LIMIT %(n)s
    ) t
),
sparse AS (
    SELECT id, row_number() OVER (ORDER BY score DESC) AS rnk
    FROM (
        SELECT c.id, ts_rank_cd(c.tsv, q.q) AS score
        FROM kb.chunks c, (SELECT to_tsquery('simple', %(tsq)s::text) AS q) q
        WHERE c.tsv @@ q.q
          AND (%(cats)s::text[] IS NULL OR c.category = ANY(%(cats)s::text[]))
          AND (%(pls)s::text[] IS NULL OR c.product_line = ANY(%(pls)s::text[]))
        ORDER BY score DESC
        LIMIT %(n)s
    ) t
)
SELECT c.id, c.doc_name, c.section, c.category, c.content,
       COALESCE(1.0 / (%(k)s + d.rnk), 0) + COALESCE(1.0 / (%(k)s + s.rnk), 0) AS rrf
FROM dense d
FULL OUTER JOIN sparse s ON d.id = s.id
JOIN kb.chunks c ON c.id = COALESCE(d.id, s.id)
ORDER BY rrf DESC
LIMIT %(n)s
"""


@dataclass
class RetrievedChunk:
    id: str
    doc_name: str
    section: str
    category: str
    content: str
    rrf_score: float
    rerank_score: float | None = None


def product_line_filter(product_line: str | None) -> list[str] | None:
    """A known product line also gets the 'general' chunks. None / 'unknown' = no filter."""
    if product_line in (None, "", "unknown"):
        return None
    return [product_line, "general"]


def build_tsquery(text: str) -> str | None:
    """OR-query from the words of the message. (AND would return nothing for long natural sentences.)"""
    tokens = re.findall(r"[\w\u0900-\u097F]+", text.lower())
    tokens = [t for t in tokens if len(t) > 1 and t not in STOPWORDS]
    tokens = list(dict.fromkeys(tokens))  # de-duplicate, keep order
    return " | ".join(tokens) if tokens else None


class Retriever:
    """Load models ONCE (at app start), not per request. Models can be injected for tests."""

    def __init__(self, conn=None, embedder=None, reranker=None):
        self.conn = conn or get_conn()
        self._embedder = embedder
        self._reranker = reranker

    @property
    def embedder(self):
        if self._embedder is None:
            from fastembed import TextEmbedding
            self._embedder = TextEmbedding(config.DENSE_MODEL)
        return self._embedder

    @property
    def reranker(self):
        if self._reranker is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            self._reranker = TextCrossEncoder(config.RERANK_MODEL)
        return self._reranker

    def embed_query(self, query: str) -> np.ndarray:
        # E5 models need the "query: " prefix (documents were embedded with "passage: ").
        vec = next(iter(self.embedder.embed([f"query: {query}"])))
        return np.asarray(vec, dtype=np.float32)

    def search(self, query: str, categories: list[str] | None = None,
               mode: str = "hybrid", limit: int | None = None, product_line: str | None = None) -> list[RetrievedChunk]:
        """mode='hybrid' (dense + keyword + RRF) or 'dense' (vectors only, used for comparison in evals)."""
        params = {
            "qvec": self.embed_query(query),
            "tsq": build_tsquery(query) if mode == "hybrid" else None,
            "cats": categories,
            "pls": product_line_filter(product_line),
            "n": limit or config.CANDIDATES,
            "k": config.RRF_K,
        }
        rows = self.conn.execute(HYBRID_SQL, params).fetchall()
        return [RetrievedChunk(str(r[0]), r[1], r[2], r[3], r[4], float(r[5])) for r in rows]

    def rerank(self, query: str, chunks: list[RetrievedChunk], top_n: int | None = None) -> list[RetrievedChunk]:
        if not chunks:
            return []
        scores = list(self.reranker.rerank(query, [c.content for c in chunks]))
        for c, s in zip(chunks, scores):
            c.rerank_score = float(s)
        ranked = sorted(chunks, key=lambda c: c.rerank_score, reverse=True)
        return ranked[: top_n or config.TOP_N]

    def retrieve(self, query: str, categories: list[str] | None = None,
                 candidates: int | None = None, top_n: int | None = None,
                 rerank: bool | None = None, product_line: str | None = None) -> list[RetrievedChunk]:
        """Full pipeline: hybrid search -> (optional) rerank -> top_n chunks for the LLM.
        rerank=None follows config.USE_RERANKER; pass True/False to force it (used by evals)."""
        cands = self.search(query, categories=categories, mode="hybrid", limit=candidates, product_line=product_line)
        use_rerank = config.USE_RERANKER if rerank is None else rerank
        if not use_rerank:
            return cands[: top_n or config.TOP_N]
        return self.rerank(query, cands, top_n)


if __name__ == "__main__":
    args = sys.argv[1:]
    product = None
    if "--product" in args:
        i = args.index("--product")
        product = args[i + 1]
        del args[i:i + 2]
    cat = None
    if "--category" in args:
        i = args.index("--category")
        cat = [args[i + 1]]
        del args[i:i + 2]
    q = " ".join(args) or "What is your return policy for electronics?"
    for r in Retriever().retrieve(q, categories=cat, product_line=product):
        score = f"rerank {r.rerank_score:.3f}" if r.rerank_score is not None else f"rrf {r.rrf_score:.4f}"
        print(f"[{score}] {r.doc_name} > {r.section}\n    {r.content[:110].replace(chr(10), ' ')}...")