#!/bin/bash
# Runs once, on first start of an empty Postgres volume.
# Two databases, two owners: n8n's internal state is kept apart from our knowledge base and audit log.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
  CREATE USER n8n WITH PASSWORD '${N8N_DB_PASSWORD}';
  CREATE DATABASE n8n OWNER n8n;
  CREATE USER kb_app WITH PASSWORD '${KB_DB_PASSWORD}';
  CREATE DATABASE kb OWNER kb_app;
  REVOKE ALL ON DATABASE kb FROM PUBLIC;
  REVOKE ALL ON DATABASE n8n FROM PUBLIC;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname kb <<-EOSQL
  CREATE EXTENSION IF NOT EXISTS vector;
EOSQL
