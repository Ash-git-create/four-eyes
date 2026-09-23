"""PostgreSQL + pgvector storage for chunks."""
import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from . import config
from .chunking import Chunk

SCHEMA_DIR = config.REPO_ROOT / "db" / "schema"


def connect(url: str, search_path: str | None = None) -> psycopg.Connection:
    options = f"-c search_path={search_path},public" if search_path else ""
    conn = psycopg.connect(url, options=options)
    register_vector(conn)
    return conn


def apply_schema(conn: psycopg.Connection) -> list[str]:
    """Apply db/schema/*.sql in name order. Every file is idempotent."""
    files = sorted(SCHEMA_DIR.glob("*.sql"))
    for f in files:
        conn.execute(f.read_text())
    conn.commit()
    return [f.name for f in files]


def migrate() -> None:
    with connect(config.database_url()) as conn:
        print("applied:", ", ".join(apply_schema(conn)))


def replace_all(conn: psycopg.Connection, chunks: list[Chunk], vectors: list[list[float]]) -> None:
    """Full rebuild in one transaction: at ~400 chunks this is simpler and safer than
    incremental upserts, and readers never see a half-loaded index."""
    with conn.transaction():
        conn.execute("TRUNCATE chunks")
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO chunks (chunk_id, source_id, source_type, title, text, source_url, lang, embedding)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                [(c.chunk_id, c.source_id, c.source_type, c.title, c.text, c.source_url, c.lang, np.array(v))
                 for c, v in zip(chunks, vectors, strict=True)],
            )


def vector_search(conn: psycopg.Connection, query_vec: list[float], limit: int,
                  source_type: str | None = None) -> list[dict]:
    """Exact cosine search (no ANN index; see db/schema/001_chunks.sql).
    source_type=None searches everything."""
    v = np.array(query_vec)
    rows = conn.execute(
        """SELECT chunk_id, source_id, source_type, title, text, source_url,
                  1 - (embedding <=> %s) AS score
           FROM chunks WHERE %s::text IS NULL OR source_type = %s
           ORDER BY embedding <=> %s LIMIT %s""",
        (v, source_type, source_type, v, limit),
    ).fetchall()
    cols = ["chunk_id", "source_id", "source_type", "title", "text", "source_url", "score"]
    return [dict(zip(cols, r)) for r in rows]
