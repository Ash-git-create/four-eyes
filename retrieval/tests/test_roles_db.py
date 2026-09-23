"""Approver allowlist and self-approval refusal, against the real Postgres.

check_approval() is SECURITY DEFINER with search_path pinned to public, so it always
reads public.approvers - a throwaway schema cannot shadow it. That is the point of the
pinning, so these tests use the real table with a unique address and remove it after.
"""
import os
import uuid

import psycopg
import pytest

from retrieval import config

pytestmark = pytest.mark.db

KEY, REQUESTER = "test-key", "customer@example.com"


@pytest.fixture
def approver_email():
    return f"pytest-{uuid.uuid4().hex[:8]}@example.invalid"


@pytest.fixture
def owner(approver_email):
    try:
        c = psycopg.connect(config.database_url(), autocommit=True, connect_timeout=3)
    except (psycopg.OperationalError, KeyError):
        pytest.skip("Postgres not reachable")
    c.execute("SELECT add_approver(%s, %s)", (approver_email, KEY))
    yield c
    c.execute("DELETE FROM approvers WHERE email = %s", (approver_email,))
    c.close()


@pytest.fixture
def n8n():
    pw = os.environ.get("N8N_APP_DB_PASSWORD")
    if not pw:
        pytest.skip("N8N_APP_DB_PASSWORD not set")
    with psycopg.connect(f"postgresql://n8n_app:{pw}@127.0.0.1:5432/kb", autocommit=True) as c:
        yield c


def check(conn, approver, key=KEY, requester=REQUESTER):
    return conn.execute("SELECT check_approval(%s, %s, %s)", (approver, key, requester)).fetchone()[0]


def test_listed_approver_with_right_key_is_ok(owner, approver_email):
    assert check(owner, approver_email) == "ok"


@pytest.mark.parametrize("kwargs", [
    {"key": "wrong"},
    {"key": ""},
    {"key": None},
    {"approver": "stranger@example.com"},
])
def test_refuses_wrong_key_or_unknown_approver(owner, approver_email, kwargs):
    assert check(owner, kwargs.pop("approver", approver_email), **kwargs) == "approval_denied"


def test_refuses_deactivated_approver(owner, approver_email):
    owner.execute("UPDATE approvers SET active = false WHERE email = %s", (approver_email,))
    assert check(owner, approver_email) == "approval_denied"


@pytest.mark.parametrize("mangle", [str, lambda e: f"  {e.upper()} "])
def test_refuses_self_approval_ignoring_case_and_spaces(owner, approver_email, mangle):
    assert check(owner, approver_email, requester=mangle(approver_email)) == "self_approval_denied"


def test_unknown_approver_is_refused_before_self_approval_is_considered(owner, approver_email):
    # Someone not on the list gets the same answer whoever the requester is,
    # so the message never reveals whether an address is an approver.
    assert check(owner, "x@example.invalid", requester="x@example.invalid") == "approval_denied"


def test_key_is_stored_hashed_not_in_clear(owner, approver_email):
    stored = owner.execute("SELECT approval_key_hash FROM approvers WHERE email = %s", (approver_email,)).fetchone()[0]
    assert KEY not in stored and len(stored) == 64


def test_add_approver_reactivates_and_replaces_key(owner, approver_email):
    owner.execute("UPDATE approvers SET active = false WHERE email = %s", (approver_email,))
    owner.execute("SELECT add_approver(%s, %s)", (approver_email, "new-key"))
    assert check(owner, approver_email, key="new-key") == "ok"
    assert check(owner, approver_email, key=KEY) == "approval_denied"


def test_n8n_role_can_ask_but_cannot_read_the_approver_list(owner, approver_email, n8n):
    assert check(n8n, approver_email) == "ok"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        n8n.execute("SELECT * FROM approvers")


def test_n8n_role_cannot_add_an_approver(owner, n8n):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        n8n.execute("SELECT add_approver('attacker@example.com', 'k')")
