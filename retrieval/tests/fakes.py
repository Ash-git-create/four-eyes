class FakeEmbedder:
    def embed_query(self, text):
        return [1.0, 0.0]


class FakeReranker:
    """Scores by a per-text lookup so tests control the rerank order."""

    def __init__(self, scores):
        self.scores = scores

    def score(self, query, texts):
        return [self.scores[t] for t in texts]


def make_hits(n, source_type="policy", prefix="c"):
    return [{"chunk_id": f"{prefix}{i}", "source_id": f"s{i}", "source_type": source_type, "title": f"t{i}",
             "text": f"{prefix}text{i}", "source_url": "u", "score": round(1 - i / 100, 2)} for i in range(n)]


class FakeVectorSearch:
    """Behaves like store.vector_search over a fixed, already-ordered hit list."""

    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def __call__(self, qvec, limit, source_type=None):
        self.calls.append((limit, source_type))
        pool = [h for h in self.hits if source_type is None or h["source_type"] == source_type]
        return pool[:limit]
