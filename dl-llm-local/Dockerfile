# syntax=docker/dockerfile:1.7
ARG MODEL=qwen3.5:9b
FROM ollama/ollama:24.04@sha256:a6149234667efc71d37766d61c1a16f24c33e4cd7a0bf4125c44a7e47e2419c4

ARG MODEL

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    /bin/ollama serve & \
    SERVE_PID=$!; \
    until /bin/ollama list >/dev/null 2>&1; do sleep 1; done; \
    /bin/ollama pull "${MODEL}"; \
    kill "${SERVE_PID}"; \
    wait "${SERVE_PID}" 2>/dev/null || true

RUN mkdir -p /etc/dato && \
    printf '%s\n' "${MODEL}" > /etc/dato/llm-model

LABEL org.dato.llm.model="${MODEL}" \
      org.dato.image.purpose="dl-llm-local — in-appliance Ollama with baked model"

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 11434
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=8 \
  CMD curl -fsS http://localhost:11434/api/tags || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["serve"]
