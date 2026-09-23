"""Thin wrappers around the embedding model and the cross-encoder.

Kept separate so tests can swap in fakes and never load a model.
"""
from . import config  # sets HF_HOME before sentence_transformers is imported

from sentence_transformers import CrossEncoder, SentenceTransformer


class Embedder:
    """multilingual-e5 expects 'query: ' / 'passage: ' prefixes; without them quality drops."""

    def __init__(self, name: str = config.EMBED_MODEL):
        self.model = SentenceTransformer(name)

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return self._encode([f"passage: {t}" for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._encode([f"query: {text}"])[0]

    def _encode(self, texts: list[str]) -> list[list[float]]:
        # Normalised vectors: cosine similarity == dot product.
        return self.model.encode(texts, normalize_embeddings=True, batch_size=32).tolist()


class Reranker:
    def __init__(self, name: str = config.RERANK_MODEL):
        self.model = CrossEncoder(name)

    def score(self, query: str, texts: list[str]) -> list[float]:
        return self.model.predict([(query, t) for t in texts]).tolist()
