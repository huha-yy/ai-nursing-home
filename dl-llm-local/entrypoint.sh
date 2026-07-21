#!/bin/sh
set -eu

MODEL_FILE="/etc/dato/llm-model"
if [ ! -f "${MODEL_FILE}" ]; then
  echo "dl-llm-local: ${MODEL_FILE} missing (image build is incomplete)" >&2
  exit 64
fi
EXPECTED_MODEL="$(cat "${MODEL_FILE}")"

export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-2}"
export OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-1}"
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-1800s}"
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-4096}"

/bin/ollama serve &
SERVE_PID=$!
RETRIES=30
while [ "${RETRIES}" -gt 0 ]; do
  if /bin/ollama list >/dev/null 2>&1; then break; fi
  RETRIES=$((RETRIES - 1))
  sleep 1
done
if [ "${RETRIES}" -eq 0 ]; then
  echo "dl-llm-local: ollama serve did not become ready in 30s" >&2
  kill "${SERVE_PID}" 2>/dev/null || true
  exit 66
fi

if ! /bin/ollama list | awk 'NR>1 {print $1}' | grep -qx "${EXPECTED_MODEL}"; then
  echo "dl-llm-local: baked model '${EXPECTED_MODEL}' missing from /root/.ollama" >&2
  /bin/ollama list || true
  kill "${SERVE_PID}" 2>/dev/null || true
  exit 65
fi

kill "${SERVE_PID}"
wait "${SERVE_PID}" 2>/dev/null || true

exec /bin/ollama "$@"
