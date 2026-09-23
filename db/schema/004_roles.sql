-- Roles and approval control.
--
-- n8n's own user model governs who can EDIT workflows and credentials (see
-- GOVERNANCE.md). This file governs who can RELEASE a draft, enforced in the
-- database so it does not depend on the n8n edition or on the workflow being
-- wired correctly.
--
-- Three roles:
--   requester - submits a question (anyone with the webhook key)
--   approver  - listed here with an approval key; only these can release a draft
--   admin     - owns the n8n instance and the kb_app database role

CREATE TABLE IF NOT EXISTS approvers (
    email             text PRIMARY KEY,
    approval_key_hash text NOT NULL,   -- sha256 of the key; the key itself is never stored
    active            boolean NOT NULL DEFAULT true,
    added_at          timestamptz NOT NULL DEFAULT now()
);

-- pgcrypto gives us digest(); it ships with the postgres image.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Widen the audit event list for the two new outcomes.
ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS audit_log_event_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_event_check CHECK (event IN (
    'request_received', 'redacted', 'retrieved', 'drafted', 'claims_checked',
    'approval_requested', 'approved', 'rejected', 'sent',
    'budget_blocked', 'error',
    'approval_denied',      -- unknown/inactive approver, or wrong key
    'self_approval_denied'  -- approver is the same person who asked
));

-- Returns 'ok', or the reason the approval must be refused. n8n calls this before
-- acting on any approve/reject decision.
--
-- Self-approval is refused by comparing the approver to the requester. The check is
-- only as good as the identity the requester supplies; with an unauthenticated
-- webhook a requester can put someone else's address in the request. GOVERNANCE.md
-- says so. The audit row makes the attempt visible either way.
CREATE OR REPLACE FUNCTION check_approval(
    p_approver text, p_key text, p_requester text
) RETURNS text LANGUAGE plpgsql STABLE
-- SECURITY DEFINER: n8n_app must be able to ASK whether an approval is valid without
-- being able to READ the approver list or the key hashes. search_path is pinned so a
-- caller cannot point the function at tables of their own.
SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
    a approvers;
BEGIN
    SELECT * INTO a FROM approvers WHERE email = lower(trim(p_approver));
    IF NOT FOUND OR NOT a.active THEN
        RETURN 'approval_denied';
    END IF;
    IF a.approval_key_hash IS DISTINCT FROM encode(digest(coalesce(p_key, ''), 'sha256'), 'hex') THEN
        RETURN 'approval_denied';
    END IF;
    IF lower(trim(p_approver)) = lower(trim(coalesce(p_requester, ''))) THEN
        RETURN 'self_approval_denied';
    END IF;
    RETURN 'ok';
END $$;

-- Admin-only helper: adds an approver from a plaintext key, storing only the hash.
CREATE OR REPLACE FUNCTION add_approver(p_email text, p_key text) RETURNS void
LANGUAGE sql AS $$
    INSERT INTO approvers (email, approval_key_hash)
    VALUES (lower(trim(p_email)), encode(digest(p_key, 'sha256'), 'hex'))
    ON CONFLICT (email) DO UPDATE
        SET approval_key_hash = excluded.approval_key_hash, active = true;
$$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'n8n_app') THEN
        -- n8n may ask whether an approval is valid; it may not read the hashes,
        -- and it may not add, remove or deactivate approvers.
        GRANT EXECUTE ON FUNCTION check_approval(text, text, text) TO n8n_app;
        REVOKE ALL ON approvers FROM n8n_app;
    END IF;
END $$;
