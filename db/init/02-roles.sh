#!/bin/bash
# Runs once on a fresh volume. The role n8n uses for the kb database: it gets
# INSERT/SELECT grants on governance tables only (see db/schema/002_governance.sql).
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
  CREATE ROLE n8n_app LOGIN PASSWORD '${N8N_APP_DB_PASSWORD}';
  GRANT CONNECT ON DATABASE kb TO n8n_app;
EOSQL
