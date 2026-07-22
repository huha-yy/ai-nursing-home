FROM python:3.12-slim

WORKDIR /app

# Install uv into the final image (needed for CMD).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
ENV UV_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
ENV UV_HTTP_TIMEOUT=300
RUN uv sync --no-dev --frozen

# Model is NOT baked into the image anymore (bge-m3 ~2.2GB would blow up image size).
# Models are loaded from a Docker named volume at /models/hf (set via HF_HOME).

COPY dl_cognee/ ./dl_cognee/

# Create user with uid 1000 (needed by PyTorch's getpass.getuser() at import time).
RUN groupadd --gid 1000 appuser && useradd --uid 1000 --gid appuser --create-home appuser

EXPOSE 8080
CMD [".venv/bin/uvicorn", "dl_cognee.main:app", "--host", "0.0.0.0", "--port", "8080"]
