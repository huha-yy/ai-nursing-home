# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple/ \
    UV_NO_CACHE=1
RUN sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 1000 --create-home --home-dir /app dlctl
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.4.30 /uv /usr/local/bin/uv

# Dependencies first (cache-friendly). dl_shared is a path dependency
# resolved at ../dl_shared relative to /app — copied to /dl_shared.
COPY dl-control/pyproject.toml dl-control/uv.lock ./
COPY dl_shared /dl_shared
ENV UV_HTTP_TIMEOUT=120
ENV UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN uv sync --frozen --no-dev --no-install-project

# Then the application source.
COPY dl-control/dl_control ./dl_control
COPY dl-control/precreated_agents /app/precreated_agents
RUN uv sync --frozen --no-dev
RUN chown -R dlctl:dlctl /app

# Put the baked venv on PATH so `uvicorn` / `python` resolve to it without
# `uv run` (which would try to re-sync against a read-only filesystem).
ENV PATH="/app/.venv/bin:$PATH"
USER dlctl
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=10 \
  CMD curl -fsS http://localhost:8080/api/health || exit 1
CMD ["uvicorn", "dl_control.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
