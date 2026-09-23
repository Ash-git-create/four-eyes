"""Retrieval modes, all behind one Searcher.

- "vector":   one global vector search.
- "rerank":   global vector top-N, then cross-encoder rerank. On the Day 3 eval this
              pushed product chunks above the policy section a question was about.
- "balanced": vector top-N per source type, rerank within each, reserve a minimum
              number of slots per source, fill the rest by rerank score. Same number
              of reranked pairs as "rerank", so latency stays comparable.

Parameters were fixed before evaluating "balanced", not tuned on the eval set.
"""
from typing import Callable, Protocol

from . import config

MODES = ("vector", "rerank", "balanced")
SOURCES = ("policy", "product")


class EmbedsQueries(Protocol):
    def embed_query(self, text: str) -> list[float]: ...


class ScoresPairs(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float]: ...


VectorSearch = Callable[..., list[dict]]  # (qvec, limit, source_type=None) -> hits


class Searcher:
    def __init__(self, embedder: EmbedsQueries, reranker: ScoresPairs, vector_search: VectorSearch,
                 candidates: int = config.RERANK_CANDIDATES,
                 min_per_source: int = config.MIN_PER_SOURCE):
        self.embedder = embedder
        self.reranker = reranker
        self.vector_search = vector_search
        self.candidates = candidates
        self.min_per_source = min_per_source

    def search(self, query: str, k: int, mode: str = "balanced") -> list[dict]:
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        qvec = self.embedder.embed_query(query)
        if mode == "vector":
            return [{**h, "rerank_score": None} for h in self.vector_search(qvec, k)]
        if mode == "rerank":
            return self._rerank(query, self.vector_search(qvec, max(k, self.candidates)))[:k]
        return self._balanced(query, qvec, k)

    def _rerank(self, query: str, hits: list[dict]) -> list[dict]:
        if not hits:
            return []
        scores = self.reranker.score(query, [h["text"] for h in hits])
        ranked = sorted(zip(hits, scores), key=lambda hs: hs[1], reverse=True)
        return [{**h, "rerank_score": s} for h, s in ranked]

    def _balanced(self, query: str, qvec: list[float], k: int) -> list[dict]:
        per_source = max(1, self.candidates // len(SOURCES))
        groups = {src: self._rerank(query, self.vector_search(qvec, per_source, source_type=src))
                  for src in SOURCES}
        reserve = min(self.min_per_source, k // len(SOURCES))
        chosen = [h for src in SOURCES for h in groups[src][:reserve]]
        rest = sorted((h for src in SOURCES for h in groups[src][reserve:]),
                      key=lambda h: h["rerank_score"], reverse=True)
        chosen += rest[:k - len(chosen)]
        return sorted(chosen, key=lambda h: h["rerank_score"], reverse=True)
