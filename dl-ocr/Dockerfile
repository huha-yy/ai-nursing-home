FROM python:3.12-slim

WORKDIR /app

# Install uv into the final image (needed for sync + CMD).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml ./
ENV UV_INDEX_URL="https://mirrors.aliyun.com/pypi/simple"
ENV UV_EXTRA_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
ENV UV_HTTP_TIMEOUT=300
RUN uv sync --no-dev

# PaddleOCR model is loaded from Docker named volume at /models (set via
# PADDLEOCR_HOME).  The model is NOT baked into this image.

COPY dl_ocr/ ./dl_ocr/

# Create non-root user (uid 1000, needed by some ML libraries).
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --create-home appuser

EXPOSE 8080
CMD [".venv/bin/uvicorn", "dl_ocr.main:app", "--host", "0.0.0.0", "--port", "8080"]
