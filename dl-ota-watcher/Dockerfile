FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv==0.4.18 -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY pyproject.toml ./
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple aiohttp asyncpg httpx "psycopg[binary]" pydantic "pydantic-settings" cryptography aiodocker "redis[hiredis]" structlog

COPY dl_ota_watcher/ /app/dl_ota_watcher/

USER 1000:1000

WORKDIR /app
ENTRYPOINT ["python", "-m", "dl_ota_watcher.main"]
