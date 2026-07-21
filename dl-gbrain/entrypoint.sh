#!/bin/bash
set -eu

# dl-gbrain entrypoint — init → import → serve.
# All steps are idempotent (safe to re-run on restart).

DB_URL="${DATABASE_URL:-postgresql://gbrain_app:${DL_GBRAIN_PG_PASSWORD}@dato-postgres:5432/gbrain}"
DB_HOST="$(echo "$DB_URL" | sed 's|.*@||;s|:.*||')"

echo "[gbrain] waiting for postgres at $DB_HOST..."
export PGPASSWORD="${DL_GBRAIN_PG_PASSWORD}"
until pg_isready -h "$DB_HOST" -U gbrain_app 2>/dev/null; do
  sleep 1
done
echo "[gbrain] postgres ready"

# Init brain (idempotent — safe to re-run).
# Uses openai recipe pointing at dl-cognee via OPENAI_BASE_URL.
# openai:text-embedding-3-large supports Matryoshka dims (incl. 1024).
echo "[gbrain] initializing brain..."
gbrain init \
  --url "$DB_URL" \
  --embedding-model openai:text-embedding-3-large \
  --embedding-dimensions 1024 \
  --non-interactive \
  --force

# Configure dl-cognee-reranker (fail-open on error).
echo "[gbrain] configuring reranker..."
gbrain config set search.reranker.model llama-server-reranker:bge-reranker-v2-m3 || true
gbrain config set search.reranker.enabled true || true
gbrain config set provider_base_urls.llama-server-reranker http://dl-cognee-reranker:8080/v1 || true

# Set search mode.
gbrain config set search.mode balanced || true

# Import brain repo data if available (idempotent — creates/updates pages).
if [ -d /brain-repo ] && [ "$(ls -A /brain-repo 2>/dev/null)" ]; then
  echo "[gbrain] importing knowledge base..."
  gbrain import /brain-repo --source company-knowledge --yes || true
  echo "[gbrain] import complete"
fi

echo "[gbrain] gbrain init done"
echo "[gbrain] starting server..."
exec gbrain serve --http --port 8080 --bind 0.0.0.0
