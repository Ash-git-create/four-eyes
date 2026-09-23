"""Governance schema against the real Postgres container, in a throwaway schema."""
import os
import psycopg
import pytest

from retrieval import config, store

pytestmark = pytest.mark.db


@pytest.fixture
def schema(test_schema):
    return test_schema


@pytest.fixture
def owner(schema):
    with psycopg.connect(config.database_url(), options=f"-c search_path={schema},public", autocommit=True) as c:
        yield c


@pytest.fixture
def n8n(schema):
    pw = os.environ.get("N8N_APP_DB_PASSWORD")
    if not pw:
        pytest.skip("N8N_APP_DB_PASSWORD not set")
    url = f"postgresql://n8n_app:{pw}@127.0.0.1:5432/kb"
    with psycopg.connect(url, options=f"-c search_path={schema},public", autocommit=True) as c:
        yield c


def add_event(conn, event="request_received"):
    conn.execute("INSERT INTO audit_log (run_id, event, actor) VALUES ('r1', %s, 'system')", (event,))


# --- audit log is append-only -------------------------------------------------

@pytest.mark.parametrize("stmt", ["UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log", "TRUNCATE audit_log"])
def test_owner_cannot_change_audit_log(owner, stmt):
    add_event(owner)
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
        owner.execute(stmt)
    assert owner.execute("SELECT count(*) FROM audit_log").fetchone()[0] == 1


def test_unknown_event_rejected(owner):
    with pytest.raises(psycopg.errors.CheckViolation):
        add_event(owner, "deleted_everything")


# --- n8n_app role: least privilege ---------------------------------------------

def test_n8n_can_insert_audit_events(n8n):
    add_event(n8n, "approved")
    assert n8n.execute("SELECT event FROM audit_log").fetchone()[0] == "approved"


@pytest.mark.parametrize("stmt", ["UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log",
                                  "TRUNCATE audit_log", "SELECT * FROM chunks",
                                  "UPDATE budget SET daily_cap_usd = 1000",
                                  "UPDATE model_prices SET input_per_mtok = 0"])
def test_n8n_has_no_privilege_for(n8n, stmt):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        n8n.execute(stmt)


# --- cost logging and budget -----------------------------------------------------

def test_log_model_call_computes_cost(n8n):
    # opus-5: 1000 in * $5/M + 500 out * $25/M = 0.005 + 0.0125
    cost = n8n.execute("SELECT log_model_call('r1', 'claude-opus-5', 1000, 500)").fetchone()[0]
    assert float(cost) == pytest.approx(0.0175)


def test_log_model_call_includes_cache_tokens(n8n):
    # 1M cache-read tokens on sonnet-5 = $0.20
    cost = n8n.execute("SELECT log_model_call('r1', 'claude-sonnet-5', 0, 0, 0, 1000000)").fetchone()[0]
    assert float(cost) == pytest.approx(0.20)


def test_free_tier_model_logs_zero_cost(n8n):
    cost = n8n.execute("SELECT log_model_call('r1', 'openai/gpt-oss-120b', 3000, 300)").fetchone()[0]
    assert float(cost) == 0.0


def test_log_model_call_rejects_unpriced_model(n8n):
    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="no price"):
        n8n.execute("SELECT log_model_call('r1', 'gpt-4o', 10, 10)")


def test_budget_blocks_once_cap_reached(owner, n8n):
    owner.execute("UPDATE budget SET daily_cap_usd = 0.03")
    assert n8n.execute("SELECT allowed FROM budget_status()").fetchone()[0] is True
    n8n.execute("SELECT log_model_call('r1', 'claude-opus-5', 1000, 500)")  # 0.0175
    assert n8n.execute("SELECT allowed FROM budget_status()").fetchone()[0] is True
    n8n.execute("SELECT log_model_call('r2', 'claude-opus-5', 1000, 500)")  # total 0.035
    spent, cap, allowed = n8n.execute("SELECT * FROM budget_status()").fetchone()
    assert (float(spent), float(cap), allowed) == (pytest.approx(0.035), 0.03, False)


def test_budget_ignores_yesterdays_spend(owner, n8n):
    owner.execute("UPDATE budget SET daily_cap_usd = 0.01")
    n8n.execute("SELECT log_model_call('r1', 'claude-opus-5', 1000, 500)")
    owner.execute("UPDATE model_calls SET occurred_at = now() - interval '1 day'")
    assert n8n.execute("SELECT allowed FROM budget_status()").fetchone()[0] is True


def test_schema_is_idempotent(schema):
    with store.connect(config.database_url(), search_path=schema) as c:
        store.apply_schema(c)
        assert c.execute("SELECT count(*) FROM model_prices").fetchone()[0] == 4
        assert c.execute("SELECT count(*) FROM budget").fetchone()[0] == 1
