import pytest

from retrieval.app import create_app


@pytest.fixture
def client():
    return create_app().test_client()


def test_health(client):
    assert client.get("/health").get_json() == {"status": "ok"}


def test_search_returns_contract_fields(client):
    resp = client.post("/search", json={"query": "return policy"})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["stub"] is True
    assert body["k"] == 5
    for r in body["results"]:
        assert set(r) == {"chunk_id", "source_id", "text", "score"}


@pytest.mark.parametrize("payload", [None, {}, {"query": ""}, {"query": "   "}, {"query": 3}])
def test_search_rejects_bad_query(client, payload):
    assert client.post("/search", json=payload).status_code == 400


@pytest.mark.parametrize("k", [0, 21, "5", True, 2.5])
def test_search_rejects_bad_k(client, k):
    assert client.post("/search", json={"query": "x", "k": k}).status_code == 400
