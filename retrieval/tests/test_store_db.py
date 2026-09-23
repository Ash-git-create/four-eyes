"""Runs against the real Postgres container, in a throwaway schema."""
import psycopg
import pytest

from retrieval import config, store
from retrieval.chunking import Chunk

pytestmark = pytest.mark.db


@pytest.fixture
def conn(test_schema):
    with store.connect(config.database_url(), search_path=test_schema) as c:
        yield c


def unit(i):
    v = [0.0] * config.EMBED_DIM
    v[i] = 1.0
    return v


def chunk(i):
    return Chunk(f"c{i}", f"s{i}", "policy", f"t{i}", f"text{i}", "u", "en")


def test_vector_search_orders_by_cosine(conn):
    store.replace_all(conn, [chunk(0), chunk(1)], [unit(0), unit(1)])
    hits = store.vector_search(conn, unit(1), limit=2)
    assert [h["chunk_id"] for h in hits] == ["c1", "c0"]
    assert hits[0]["score"] == pytest.approx(1.0)
    assert hits[1]["score"] == pytest.approx(0.0)


def test_replace_all_is_a_full_rebuild(conn):
    store.replace_all(conn, [chunk(0), chunk(1)], [unit(0), unit(1)])
    store.replace_all(conn, [chunk(2)], [unit(2)])
    assert conn.execute("SELECT array_agg(chunk_id) FROM chunks").fetchone()[0] == ["c2"]


def test_schema_rejects_unknown_source_type(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        store.replace_all(conn, [Chunk("x", "x", "email", "t", "t", "u", None)], [unit(0)])


def test_vector_search_filters_by_source_type(conn):
    product = Chunk("x0", "x0", "product", "t", "t", "u", "de")
    store.replace_all(conn, [chunk(0), product], [unit(0), unit(0)])
    assert [h["chunk_id"] for h in store.vector_search(conn, unit(0), 5, "product")] == ["x0"]
    assert [h["chunk_id"] for h in store.vector_search(conn, unit(0), 5, "policy")] == ["c0"]
    assert len(store.vector_search(conn, unit(0), 5)) == 2
