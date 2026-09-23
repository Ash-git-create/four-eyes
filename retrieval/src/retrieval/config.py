"""Settings from environment variables, with defaults for local development.

Reads the repo-root .env (same file docker compose uses) so the DB password
lives in one place. Model files are cached inside the repo (.cache/, gitignored)
so `rm -rf .cache` removes every downloaded model.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(REPO_ROOT / ".env")
os.environ.setdefault("HF_HOME", str(REPO_ROOT / ".cache" / "huggingface"))

EMBED_MODEL = os.environ.get("EMBED_MODEL", "intfloat/multilingual-e5-small")
EMBED_DIM = 384
RERANK_MODEL = os.environ.get("RERANK_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")
# How many vector-search candidates the reranker sees. Fixed here so eval runs are comparable.
RERANK_CANDIDATES = int(os.environ.get("RERANK_CANDIDATES", "20"))
# "balanced" mode: slots guaranteed to each source type in the final top k.
MIN_PER_SOURCE = int(os.environ.get("MIN_PER_SOURCE", "2"))


def database_url() -> str:
    if url := os.environ.get("KB_DATABASE_URL"):
        return url
    password = os.environ["KB_DB_PASSWORD"]
    return f"postgresql://kb_app:{password}@127.0.0.1:5432/kb"
