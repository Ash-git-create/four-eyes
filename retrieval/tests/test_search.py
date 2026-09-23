import pytest

from retrieval.search import Searcher

from fakes import FakeEmbedder, FakeReranker, FakeVectorSearch, make_hits

POLICY = make_hits(10, "policy", "p")
PRODUCT = make_hits(10, "product", "x")
# vector order puts policies first; reranker prefers products (the Day 3 failure)
ALL = POLICY + PRODUCT
PRODUCT_LOVING = {**{h["text"]: -1.0 - i for i, h in enumerate(POLICY)},
                  **{h["text"]: 5.0 - i for i, h in enumerate(PRODUCT)}}


def searcher(hits=ALL, scores=PRODUCT_LOVING, candidates=20, min_per_source=2):
    vs = FakeVectorSearch(hits)
    return Searcher(FakeEmbedder(), FakeReranker(scores), vs, candidates, min_per_source), vs


def ids(results):
    return [r["chunk_id"] for r in results]


def test_vector_mode_keeps_vector_order_and_fetches_k_globally():
    s, vs = searcher()
    results = s.search("q", k=3, mode="vector")
    assert ids(results) == ["p0", "p1", "p2"]
    assert vs.calls == [(3, None)]
    assert all(r["rerank_score"] is None for r in results)


def test_rerank_mode_reorders_global_candidate_pool():
    s, vs = searcher()
    assert ids(s.search("q", k=3, mode="rerank")) == ["x0", "x1", "x2"]
    assert vs.calls == [(20, None)]


def test_rerank_keeps_vector_score_alongside_rerank_score():
    s, _ = searcher()
    top = s.search("q", k=1, mode="rerank")[0]
    assert top["score"] == 1.0 and top["rerank_score"] == 5.0


def test_balanced_reserves_slots_for_each_source():
    s, _ = searcher()
    results = s.search("q", k=5, mode="balanced")
    # reranker prefers every product, but 2 policy slots are guaranteed
    assert ids(results) == ["x0", "x1", "x2", "p0", "p1"]


def test_balanced_splits_candidate_budget_across_sources():
    s, vs = searcher(candidates=20)
    s.search("q", k=5, mode="balanced")
    assert sorted(vs.calls, key=str) == sorted([(10, "policy"), (10, "product")], key=str)


def test_balanced_fills_by_score_when_one_source_is_better():
    policy_loving = {t: -v for t, v in PRODUCT_LOVING.items()}
    s, _ = searcher(scores=policy_loving)
    results = s.search("q", k=5, mode="balanced")
    assert sum(r["source_type"] == "policy" for r in results) == 3
    assert sum(r["source_type"] == "product" for r in results) == 2


def test_balanced_with_k1_has_no_reserved_slot():
    s, _ = searcher()
    assert ids(s.search("q", k=1, mode="balanced")) == ["x0"]


def test_balanced_handles_empty_source():
    s, _ = searcher(hits=PRODUCT)
    assert len(s.search("q", k=5, mode="balanced")) == 5


def test_unknown_mode_rejected():
    s, _ = searcher()
    with pytest.raises(ValueError):
        s.search("q", k=5, mode="hybrid")
