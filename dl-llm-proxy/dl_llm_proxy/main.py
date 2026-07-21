"""dl-llm-proxy — OpenAI-compatible passthrough with P6 auth/audit/rate-limit.

P4 baseline: thin httpx passthrough fronting LOCAL_LLM_BASE_URL.
P6 adds: per-agent DL_INTERNAL_TOKEN verification, tier=tier1 enforcement,
inbound bearer stripping, audit POST to dl-control, per-agent rate limit.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from dl_llm_proxy.auth import build_auth_middleware
from dl_llm_proxy.rate_limit import build_llm_proxy_rate_limit_key
from dl_llm_proxy.settings import load_settings


def _redact_bearer_processor(_logger, _method, event_dict):
    for key in list(event_dict.keys()):
        val = event_dict[key]
        if key.lower() == "authorization" or (isinstance(val, str) and val.startswith("Bearer ")):
            event_dict[key] = "Bearer ***"
    return event_dict


def _configure_logging():
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_bearer_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


_configure_logging()


def build_app() -> FastAPI:
    settings = load_settings()
    app = FastAPI(title="dl-llm-proxy", version="0.2.0")

    # Rate limit middleware (innermost) — wraps the handler.
    if settings.redis_url:
        import redis.asyncio as aioredis
        from dl_shared.rate_limit import RateLimitMiddleware

        redis_client = aioredis.from_url(
            settings.redis_url.get_secret_value(),
        )
        app.add_middleware(
            RateLimitMiddleware,
            redis=redis_client,
            max_requests=settings.dl_llm_proxy_rpm,
            window_seconds=60,
            key_fn=build_llm_proxy_rate_limit_key,
            prefix="dato:",
        )

    # Audit middleware (middle) — wraps rate limit so 429 rejections are audited.
    from dl_llm_proxy.audit import AuditASGIMiddleware

    app.add_middleware(AuditASGIMiddleware, settings=settings)

    # Auth middleware (outermost) — runs first, strips bearer, sets agent_id.
    app.middleware("http")(build_auth_middleware(settings))

    metrics_state: dict = {"request_count": 0, "last_request_at": None}

    @app.middleware("http")
    async def _metrics(request, call_next):
        response = await call_next(request)
        if (
            request.url.path != "/healthz"
            and getattr(request.state, "agent_id", None) is not None
            and 200 <= response.status_code < 300
        ):
            metrics_state["request_count"] += 1
            metrics_state["last_request_at"] = datetime.now(UTC).isoformat()
        return response

    upstream_timeout = httpx.Timeout(60.0, connect=5.0)
    base_headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.local_llm_api_key:
        base_headers["Authorization"] = f"Bearer {settings.local_llm_api_key.get_secret_value()}"

    @app.api_route(
        "/v1/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    )
    async def proxy(path: str, request: Request):
        upstream_url = f"{settings.local_llm_base_url}/{path}"
        headers = dict(base_headers)
        body = await request.body()

        is_stream = False
        if request.method == "POST":
            try:
                import json as _json

                is_stream = _json.loads(body or b"{}").get("stream", False)
            except Exception:
                pass

        if is_stream:
            client = httpx.AsyncClient(timeout=upstream_timeout)
            upstream_resp = await client.send(
                client.build_request(
                    method=request.method,
                    url=upstream_url,
                    headers=headers,
                    content=body,
                ),
                stream=True,
            )
            ct = upstream_resp.headers.get("content-type", "")
            if "text/event-stream" in ct:

                async def _gen():
                    try:
                        async for chunk in upstream_resp.aiter_bytes():
                            yield chunk
                    finally:
                        await client.aclose()

                return StreamingResponse(
                    _gen(),
                    status_code=upstream_resp.status_code,
                    headers={
                        "Content-Type": "text/event-stream",
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                    },
                )
            data = upstream_resp.read()
            await client.aclose()
            return Response(
                content=data, status_code=upstream_resp.status_code, headers={"Content-Type": ct}
            )

        async with httpx.AsyncClient(timeout=upstream_timeout) as client:
            try:
                upstream_resp = await client.request(
                    method=request.method,
                    url=upstream_url,
                    headers=headers,
                    content=body,
                )
            except httpx.RequestError as exc:
                return JSONResponse(
                    status_code=502,
                    content={
                        "error": {"message": f"upstream unreachable: {exc}", "type": "proxy_error"}
                    },
                )

        return Response(
            content=upstream_resp.read(),
            status_code=upstream_resp.status_code,
            headers={"Content-Type": upstream_resp.headers.get("content-type", "")},
        )

    @app.get("/healthz")
    async def healthz():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as c:
                r = await c.get(settings.local_llm_base_url)
                upstream_reachable = r.status_code < 500
        except httpx.RequestError:
            upstream_reachable = False
        return {
            "status": "ok" if upstream_reachable else "degraded",
            "upstream_reachable": upstream_reachable,
            "last_request_at": metrics_state["last_request_at"],
            "request_count": metrics_state["request_count"],
        }

    return app


app = build_app()
