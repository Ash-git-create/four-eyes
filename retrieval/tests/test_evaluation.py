import pytest

from retrieval.evaluation import first_gold_rank, hit_at, reciprocal_rank, run, summarise
from retrieval.search import Searcher

from fakes import FakeEmbedder, FakeReranker, FakeVectorSearch, make_hits


def test_first_gold_rank_is_one_based_and_uses_first_match():
    assert first_gold_rank(["a", "b", "c"], {"c", "b"}) == 2


def test_first_gold_rank_none_when_missing():
    assert first_gold_rank(["a", "b"], {"z"}) is None


@pytest.mark.parametrize("rank,k,expected", [(1, 1, 1.0), (5, 5, 1.0), (6, 5, 0.0), (None, 5, 0.0)])
def test_hit_at(rank, k, expected):
    assert hit_at(rank, k) == expected


def test_reciprocal_rank():
    assert reciprocal_rank(4) == 0.25
    assert reciprocal_rank(None) == 0.0


def test_summarise():
    s = summarise([{"rank": 1}, {"rank": 2}, {"rank": None}, {"rank": 6}])
    assert s == {"n": 4, "hit@1": 0.25, "hit@5": 0.5, "mrr@5": round((1 + 0.5 + 0 + 1 / 6) / 4, 3)}


def test_run_excludes_unanswerable_and_aggregate_from_metrics():
    hits = make_hits(12)
    searcher = Searcher(FakeEmbedder(), FakeReranker({h["text"]: -i for i, h in enumerate(hits)}),
                        FakeVectorSearch(hits), candidates=12)
    questions = [
        {"id": "q1", "question": "x", "lang": "en", "type": "policy", "gold_chunk_ids": ["c1"]},
        {"id": "q2", "question": "x", "lang": "de", "type": "unanswerable", "gold_chunk_ids": []},
        {"id": "q3", "question": "x", "lang": "en", "type": "aggregate", "gold_chunk_ids": []},
    ]
    report = run(searcher, questions, mode="rerank")
    assert report["overall"] == {"n": 1, "hit@1": 0.0, "hit@5": 1.0, "mrr@5": 0.5}
    assert all(len(p["retrieved"]) <= 5 for p in report["per_question"])
    assert report["top1_score_unanswerable"] == {"min": 0, "median": 0, "max": 0}
    assert len(report["per_question"]) == 3
