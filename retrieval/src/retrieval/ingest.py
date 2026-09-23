"""Rebuild the chunks table from data/raw (OFF snapshots) and docs/samples (policies).

Usage: uv run ingest
"""
import glob
import json
import time
from pathlib import Path

from . import config, store
from .chunking import Chunk, policy_to_chunks, product_to_chunk


def load_chunks(repo: Path = config.REPO_ROOT) -> tuple[list[Chunk], dict]:
    seen, chunks, stats = set(), [], {"products_raw": 0, "products_skipped_no_name": 0, "products_duplicate": 0}
    for f in sorted(glob.glob(str(repo / "data" / "raw" / "off_*.json"))):
        for p in json.loads(Path(f).read_text(encoding="utf-8"))["products"]:
            stats["products_raw"] += 1
            c = product_to_chunk(p)
            if c is None:
                stats["products_skipped_no_name"] += 1
            elif c.chunk_id in seen:
                stats["products_duplicate"] += 1  # same product in both categories
            else:
                seen.add(c.chunk_id)
                chunks.append(c)
    stats["product_chunks"] = len(chunks)
    for path in sorted((repo / "docs" / "samples").glob("*.md")):
        chunks.extend(policy_to_chunks(path.relative_to(repo), source_id=f"sample-{path.stem}"))
    stats["policy_chunks"] = len(chunks) - stats["product_chunks"]
    return chunks, stats


def main() -> None:
    import os
    os.chdir(config.REPO_ROOT)  # policy paths are repo-relative
    from .models import Embedder

    chunks, stats = load_chunks()
    embedder = Embedder()
    t0 = time.perf_counter()  # encoding only, not model loading
    vectors = embedder.embed_passages([c.text for c in chunks])
    stats["embed_seconds"] = round(time.perf_counter() - t0, 1)
    with store.connect(config.database_url()) as conn:
        store.apply_schema(conn)
        store.replace_all(conn, chunks, vectors)
        stats["rows_in_db"] = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()
