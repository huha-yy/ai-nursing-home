FROM python:3.12-slim

WORKDIR /app

# Install uv.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
ENV UV_INDEX_URL="https://mirrors.aliyun.com/pypi/simple"
ENV UV_EXTRA_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
# Sync WITHOUT torch/pytorch to avoid pulling CUDA nvidia-* packages (~2GB).
RUN uv sync --no-dev --frozen

# Install CPU-only PyTorch via uv (which manages the venv).
RUN uv pip install --no-cache-dir --python .venv/bin/python \
    torch --index-url https://download.pytorch.org/whl/cpu

COPY dl_cognee_reranker/ ./dl_cognee_reranker/

# Create user with uid 1000 (needed by PyTorch's getpass.getuser() at import time).
RUN groupadd --gid 1000 appuser && useradd --uid 1000 --gid appuser --create-home appuser

EXPOSE 8080
CMD [".venv/bin/uvicorn", "dl_cognee_reranker.main:app", "--host", "0.0.0.0", "--port", "8080"]
