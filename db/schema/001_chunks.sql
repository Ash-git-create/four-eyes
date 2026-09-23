-- Retrieval corpus. Owned by kb_app; applied by `uv run migrate` and by ingest (idempotent).
-- No ANN index (HNSW/IVFFlat) on purpose: with a few hundred rows an exact scan is
-- fast and gives exact results. Revisit if the corpus grows to tens of thousands.
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     text PRIMARY KEY,
    source_id    text NOT NULL,
    source_type  text NOT NULL CHECK (source_type IN ('product', 'policy')),
    title        text NOT NULL,
    text         text NOT NULL,
    source_url   text NOT NULL,
    lang         text,
    embedding    vector(384) NOT NULL,
    ingested_at  timestamptz NOT NULL DEFAULT now()
);
