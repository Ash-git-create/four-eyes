import sys
import uuid
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).parent))  # lets tests import fakes.py

from retrieval import config, store  # noqa: E402

TABLES = ("chunks", "claims", "audit_log", "model_calls", "model_prices", "budget")


@pytest.fixture
def test_schema():
    """A throwaway schema with the full schema applied.

    search_path is '<schema>, public' (pgvector's type lives in public), so a table
    missing from the test schema would silently resolve to the REAL table in public.
    That happened once and wiped the corpus; the guard below makes it impossible.
    """
    name = f"test_{uuid.uuid4().hex[:8]}"
    try:
        admin = psycopg.connect(config.database_url(), autocommit=True, connect_timeout=3)
    except (psycopg.OperationalError, KeyError):
        pytest.skip("Postgres not reachable")
    admin.execute(f"CREATE SCHEMA {name}")
    try:
        with store.connect(config.database_url(), search_path=name) as c:
            store.apply_schema(c)
            for table in TABLES:
                resolved = c.execute(
                    "SELECT n.nspname FROM pg_class t JOIN pg_namespace n ON n.oid = t.relnamespace "
                    "WHERE t.oid = to_regclass(%s)", (table,)).fetchone()
                if not resolved or resolved[0] != name:
                    pytest.fail(f"{table} resolves to {resolved}, not test schema {name}; refusing to run")
        yield name
    finally:
        admin.execute(f"DROP SCHEMA {name} CASCADE")
        admin.close()
