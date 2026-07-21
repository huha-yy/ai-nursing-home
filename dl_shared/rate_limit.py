"""Redis-backed sliding-window rate limiting middleware."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp


def default_key_fn(request: Request) -> str:
    header_val = request.headers.get("X-DL-User-Id")
    if header_val:
        return header_val
    if request.client:
        return request.client.host
    return "unknown"


_RATE_LIMIT_LUA = """
local window_start = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, window_start)
local count = redis.call('ZCARD', KEYS[1])
local limit = tonumber(ARGV[2])
if count < limit then
    local score = tonumber(ARGV[3])
    redis.call('ZADD', KEYS[1], score, ARGV[4])
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[5]))
    return {count + 1, 1}
else
    return {count, 0}
end
"""


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis-backed sliding-window rate limiter.

    Limits requests per-key using a Redis sorted set as a sliding window.
    Uses a Lua script for atomic check-and-increment.
    """

    def __init__(
        self,
        app: ASGIApp,
        redis,  # redis.asyncio.Redis
        *,
        max_requests: int,
        window_seconds: int,
        key_fn: Callable[[Request], str] | None = None,
        prefix: str = "",
    ) -> None:
        super().__init__(app)
        self._redis = redis
        self._max = max_requests
        self._window = window_seconds
        self._key_fn = key_fn or default_key_fn
        self._prefix = prefix

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        raw_key = self._key_fn(request)
        if not raw_key:
            return await call_next(request)

        key = f"{self._prefix}{raw_key}"
        now = time.time()
        window_start = now - self._window
        member = str(time.monotonic_ns())

        try:
            result = await self._redis.eval(
                _RATE_LIMIT_LUA,
                1,
                key,
                window_start,
                self._max,
                now,
                member,
                self._window,
            )
            allowed = bool(result[1])
            if not allowed:
                return JSONResponse(
                    {"detail": "Too many requests"},
                    status_code=429,
                    headers={"Retry-After": str(self._window)},
                )
        except Exception:
            return JSONResponse(
                {"detail": "Rate limiting unavailable"},
                status_code=503,
            )

        return await call_next(request)
