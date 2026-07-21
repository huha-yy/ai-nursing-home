FROM oven/bun:1 AS builder

WORKDIR /app
COPY . .

# Build admin SPA first, then compile the CLI binary.
RUN bun install && \
    cd admin && bun install && cd .. && \
    bun run build:admin && \
    bun run build

# Runtime image — slim Debian with common tools (pg_isready, sed, etc.).
FROM debian:12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/bin/gbrain /usr/local/bin/gbrain
COPY --from=builder /app/entrypoint.sh /entrypoint.sh
COPY brain-repo/ /brain-repo/

RUN useradd --uid 1000 --create-home appuser && \
    mkdir -p /app && chown -R appuser:appuser /app

USER appuser

EXPOSE 8080
CMD ["/bin/bash", "/entrypoint.sh"]
