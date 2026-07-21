FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_NO_SYNC=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 1000 --create-home --home-dir /app llmproxy
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.4.30 /uv /usr/local/bin/uv

COPY dl-llm-proxy/pyproject.toml ./
COPY dl_shared/ /dl_shared/
RUN uv sync --no-dev --no-install-project --no-editable

COPY dl-llm-proxy/dl_llm_proxy/ ./dl_llm_proxy/
RUN uv sync --no-dev --no-editable && chown -R llmproxy:llmproxy /app

USER llmproxy
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=10 \
  CMD curl -fsS http://localhost:8080/healthz || exit 1

CMD ["uv", "run", "uvicorn", "dl_llm_proxy.main:app", "--host", "0.0.0.0", "--port", "8080"]
