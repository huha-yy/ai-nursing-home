"""Tests for dl_shared.rate_limit.RateLimitMiddleware.

Unit tests using a mock async Redis (unittest.mock.AsyncMock).
For full integration coverage, the smoke tests exercise the real Redis stack.
"""

from unittest.mock import AsyncMock

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient


async def dummy_ok(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@pytest.fixture
def mock_redis():
    """AsyncMock that simulates Redis Lua eval returning [count, allowed]."""
    redis = AsyncMock()
    # eval returns [count, 1] (1 = allowed) for first 2 calls, then [count, 0] (blocked)
    redis.eval.side_effect = [
        [1, 1],  # first request: count=1, allowed
        [2, 1],  # second request: count=2, allowed
        [3, 0],  # third request: count=3, blocked
    ]
    return redis


@pytest.fixture
def app_with_limiter(mock_redis):
    from dl_shared.rate_limit import RateLimitMiddleware

    app = Starlette()
    app.add_middleware(
        RateLimitMiddleware,
        redis=mock_redis,
        max_requests=2,
        window_seconds=60,
        prefix="test:",
    )
    app.add_route("/", dummy_ok, methods=["GET"])
    return app


def test_allows_requests_within_limit(app_with_limiter):
    client = TestClient(app_with_limiter)
    r1 = client.get("/")
    assert r1.status_code == 200
    r2 = client.get("/")
    assert r2.status_code == 200


def test_blocks_requests_over_limit(app_with_limiter):
    client = TestClient(app_with_limiter)
    client.get("/")
    client.get("/")
    r3 = client.get("/")
    assert r3.status_code == 429
    assert "Retry-After" in r3.headers


def test_503_on_redis_failure(mock_redis):
    """Middleware returns 503 when Redis raises an exception."""
    from dl_shared.rate_limit import RateLimitMiddleware

    mock_redis.eval.side_effect = RuntimeError("connection lost")

    app = Starlette()
    app.add_middleware(
        RateLimitMiddleware,
        redis=mock_redis,
        max_requests=2,
        window_seconds=60,
        prefix="test:",
    )
    app.add_route("/", dummy_ok, methods=["GET"])

    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 503
