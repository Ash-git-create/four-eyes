-- Governance tables: append-only audit log, model-call cost log, daily budget.
-- Applied by `uv run migrate` as kb_app (the owner). Idempotent.
--
-- n8n connects as n8n_app (created by db/init/02-roles.sh), which can only INSERT
-- into audit_log and model_calls and read the budget. Two layers protect the log:
--   1. privileges: n8n_app has no UPDATE/DELETE/TRUNCATE grant at all;
--   2. triggers: even the owner's normal UPDATE/DELETE/TRUNCATE is rejected.
-- Limit (say it in interview): the table owner or a superuser can still drop the
-- trigger. Append-only here means "against the application", not "tamper-proof".

CREATE TABLE IF NOT EXISTS audit_log (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at  timestamptz NOT NULL DEFAULT now(),
    run_id       text NOT NULL,       -- n8n execution id: joins the log to one workflow run
    event        text NOT NULL CHECK (event IN (
                     'request_received', 'redacted', 'retrieved', 'drafted', 'claims_checked',
                     'approval_requested', 'approved', 'rejected', 'sent',
                     'budget_blocked', 'error')),
    actor        text NOT NULL,       -- 'system' or the approver's identity
    details      jsonb NOT NULL DEFAULT '{}'::jsonb  -- redacted content only, never raw PII
);

CREATE OR REPLACE FUNCTION audit_log_reject_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;

-- Row triggers do not fire on TRUNCATE, so it needs its own statement trigger.
CREATE OR REPLACE TRIGGER audit_log_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_reject_change();
CREATE OR REPLACE TRIGGER audit_log_no_truncate
    BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_reject_change();

-- Prices per million tokens (USD). Cost is computed once, at insert, and stored,
-- so a later price change never rewrites historical costs.
CREATE TABLE IF NOT EXISTS model_prices (
    model                 text PRIMARY KEY,
    input_per_mtok        numeric(10, 4) NOT NULL,
    output_per_mtok       numeric(10, 4) NOT NULL,
    cache_write_per_mtok  numeric(10, 4) NOT NULL,  -- 1.25x input (5-minute cache)
    cache_read_per_mtok   numeric(10, 4) NOT NULL,  -- 0.1x input
    source                text NOT NULL
);
INSERT INTO model_prices VALUES
    ('claude-opus-5',    5, 25, 6.25, 0.50, 'claude-api skill price table, cached 2026-06-24; verify on anthropic.com/pricing'),
    ('claude-sonnet-5',  2, 10, 2.50, 0.20, 'claude-api skill price table, cached 2026-06-24; verify on anthropic.com/pricing'),
    ('claude-haiku-4-5', 1,  5, 1.25, 0.10, 'claude-api skill price table, cached 2026-06-24; verify on anthropic.com/pricing'),
    -- Development only: Groq free tier has no per-token charge, so cost_usd is 0 for
    -- these runs. Token counts are still real; the daily cap cannot bind on them.
    ('openai/gpt-oss-120b', 0, 0, 0, 0, 'Groq free tier (no per-token charge) - development substitute for Claude')
ON CONFLICT (model) DO UPDATE SET
    input_per_mtok = EXCLUDED.input_per_mtok, output_per_mtok = EXCLUDED.output_per_mtok,
    cache_write_per_mtok = EXCLUDED.cache_write_per_mtok, cache_read_per_mtok = EXCLUDED.cache_read_per_mtok,
    source = EXCLUDED.source;

CREATE TABLE IF NOT EXISTS model_calls (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at           timestamptz NOT NULL DEFAULT now(),
    run_id                text NOT NULL,
    model                 text NOT NULL REFERENCES model_prices (model),
    input_tokens          integer NOT NULL CHECK (input_tokens >= 0),
    output_tokens         integer NOT NULL CHECK (output_tokens >= 0),
    cache_write_tokens    integer NOT NULL DEFAULT 0 CHECK (cache_write_tokens >= 0),
    cache_read_tokens     integer NOT NULL DEFAULT 0 CHECK (cache_read_tokens >= 0),
    cost_usd              numeric(12, 6) NOT NULL,
    latency_ms            integer
);

CREATE TABLE IF NOT EXISTS budget (
    id             boolean PRIMARY KEY DEFAULT true CHECK (id),  -- exactly one row
    daily_cap_usd  numeric(10, 2) NOT NULL CHECK (daily_cap_usd >= 0),
    timezone       text NOT NULL DEFAULT 'Europe/Berlin'
);
INSERT INTO budget (daily_cap_usd) VALUES (2.00) ON CONFLICT (id) DO NOTHING;

-- Called by n8n right after each Claude response, with the usage fields from the API.
CREATE OR REPLACE FUNCTION log_model_call(
    p_run_id text, p_model text, p_input integer, p_output integer,
    p_cache_write integer DEFAULT 0, p_cache_read integer DEFAULT 0, p_latency_ms integer DEFAULT NULL
) RETURNS numeric LANGUAGE plpgsql AS $$
DECLARE
    pr model_prices;
    cost numeric;
BEGIN
    SELECT * INTO pr FROM model_prices WHERE model = p_model;
    IF NOT FOUND THEN
        -- Fail loudly: an unpriced model would otherwise be invisible to the budget.
        RAISE EXCEPTION 'no price for model %', p_model USING ERRCODE = 'foreign_key_violation';
    END IF;
    cost := (p_input * pr.input_per_mtok + p_output * pr.output_per_mtok
             + p_cache_write * pr.cache_write_per_mtok + p_cache_read * pr.cache_read_per_mtok) / 1e6;
    INSERT INTO model_calls (run_id, model, input_tokens, output_tokens, cache_write_tokens,
                             cache_read_tokens, cost_usd, latency_ms)
    VALUES (p_run_id, p_model, p_input, p_output, p_cache_write, p_cache_read, cost, p_latency_ms);
    RETURN cost;
END $$;

-- Called by n8n BEFORE each Claude call; an IF node stops the run when allowed = false.
-- Known limit: two runs checking at the same moment can both pass, so the cap can be
-- overshot by at most the cost of the calls in flight. Acceptable for a daily demo cap.
CREATE OR REPLACE FUNCTION budget_status()
RETURNS TABLE (spent_today_usd numeric, daily_cap_usd numeric, allowed boolean)
LANGUAGE sql STABLE AS $$
    SELECT coalesce(sum(c.cost_usd), 0), b.daily_cap_usd, coalesce(sum(c.cost_usd), 0) < b.daily_cap_usd
    FROM budget b
    LEFT JOIN model_calls c
      ON (c.occurred_at AT TIME ZONE b.timezone)::date = (now() AT TIME ZONE b.timezone)::date
    GROUP BY b.daily_cap_usd;
$$;

-- Least privilege for n8n. Wrapped so the file also applies where the role is absent.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'n8n_app') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA ' || quote_ident(current_schema()) || ' TO n8n_app';
        GRANT INSERT, SELECT ON audit_log TO n8n_app;
        GRANT INSERT ON model_calls TO n8n_app;
        GRANT SELECT ON model_prices, budget TO n8n_app;
        GRANT SELECT ON model_calls TO n8n_app;  -- budget_status() runs as the caller and sums it
        GRANT EXECUTE ON FUNCTION log_model_call(text, text, integer, integer, integer, integer, integer),
                                  budget_status() TO n8n_app;
    END IF;
END $$;
