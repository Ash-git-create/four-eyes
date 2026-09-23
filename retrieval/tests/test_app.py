import pytest

from retrieval.app import create_app
from retrieval.search import Searcher

from fakes import FakeEmbedder, FakeReranker, FakeVectorSearch, make_hits


@pytest.fixture
def client():
    hits = make_hits(3, "policy", "p") + make_hits(3, "product", "x")
    searcher = Searcher(FakeEmbedder(), FakeReranker({h["text"]: 0.0 for h in hits}),
                        FakeVectorSearch(hits), candidates=6)
    return create_app(searcher).test_client()


def test_health(client):
    assert client.get("/health").get_json() == {"status": "ok"}


def test_search_returns_contract_fields(client):
    resp = client.post("/search", json={"query": "return policy", "k": 2})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["k"] == 2 and body["mode"] == "balanced"
    assert len(body["results"]) == 2
    for r in body["results"]:
        assert set(r) == {"chunk_id", "source_id", "source_type", "title", "text",
                          "source_url", "score", "rerank_score"}


def test_vector_mode_has_null_rerank_score(client):
    body = client.post("/search", json={"query": "x", "mode": "vector"}).get_json()
    assert body["mode"] == "vector"
    assert all(r["rerank_score"] is None for r in body["results"])


@pytest.mark.parametrize("payload", [None, {}, {"query": ""}, {"query": "   "}, {"query": 3}])
def test_search_rejects_bad_query(client, payload):
    assert client.post("/search", json=payload).status_code == 400


@pytest.mark.parametrize("k", [0, 21, "5", True, 2.5])
def test_search_rejects_bad_k(client, k):
    assert client.post("/search", json={"query": "x", "k": k}).status_code == 400


@pytest.mark.parametrize("mode", ["hybrid", 1, None, True])
def test_search_rejects_bad_mode(client, mode):
    assert client.post("/search", json={"query": "x", "mode": mode}).status_code == 400


def test_redact_endpoint_returns_counts_not_values(client):
    body = client.post("/redact", json={"text": "write to x@y.de"}).get_json()
    assert body == {"text": "write to [EMAIL]", "redactions": {"EMAIL": 1}}


def test_redact_rejects_non_string(client):
    assert client.post("/redact", json={"text": 5}).status_code == 400
