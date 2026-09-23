#!/bin/bash
# Prints the resume URL for every waiting n8n execution.
# Dev convenience only: in a real system the approver gets this link by email or
# from an approval queue, never by reading n8n's database.
set -euo pipefail
cd "$(dirname "$0")/.."
for id in $(docker compose exec -T postgres psql -U postgres -d n8n -At \
              -c "select id from execution_entity where status = 'waiting' order by id"); do
  url=$(docker compose exec -T postgres psql -U postgres -d n8n -At \
          -c "select data from execution_data where \"executionId\" = $id" \
        | grep -oE 'https?://[^"\\ ]*webhook-waiting[^"\\ ]*' | head -1)
  echo "execution $id: ${url:-<no resume URL found>}"
done
