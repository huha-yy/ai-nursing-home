"""P6 — auth middleware: bearer parse + dl-control verify + tier check + LRU cache."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable

import httpx
import structlog
from fastapi import Request
from fastapi.responses import JSONResponse

logger = structlog.get_logger()

_PUBLIC_PATHS = frozenset({"/healthz"})


class _TTLCache:
    """Tiny LRU+TTL cache for token -> (agent_id, tier)."""

    def __init__(self, max_entries: int, ttl_seconds: float):
        self._entries: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._max = max_entries
        self._ttl = ttl_seconds

    def get(self, key: str) -> dict | None:
        now = time.monotonic()
        try:
            ts, value = self._entries[key]
        except KeyError:
            return None
        if now - ts > self._ttl:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return value

    def set(self, key: str, value: dict) -> None:
        self._entries[key] = (time.monotonic(), value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)


def build_auth_middleware(settings) -> Callable:
    cache = _TTLCache(
        max_entries=settings.auth_cache_max_entries,
        ttl_seconds=settings.auth_cache_ttl_seconds,
    )

    async def middleware(request: Request, call_next: Callable[[Request], Awaitable]):
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer ") or len(auth_header) <= len("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"error": {"message": "missing or malformed bearer", "type": "auth_error"}},
            )
        token = auth_header[len("Bearer ") :]

        cached = cache.get(token)
        if cached is None:
            verify_url = f"{settings.dl_control_url}/api/agent/verify"
            internal_key = settings.dl_internal_api_key.get_secret_value()
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as c:
                    r = await c.post(
                        verify_url,
                        headers={
                            "Authorization": f"Bearer {internal_key}",
                            "Content-Type": "application/json",
                        },
                        json={"token": token},
                    )
            except httpx.RequestError as exc:
                logger.error("auth_verify_unreachable", error=str(exc))
                return JSONResponse(
                    status_code=502,
                    content={
                        "error": {"message": "auth backend unreachable", "type": "proxy_error"}
                    },
                )
            if r.status_code != 200:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": {"message": "token verification failed", "type": "auth_error"}
                    },
                )
            data = r.json()
            cached = {"agent_id": data.get("agent_id"), "tier": data.get("tier")}
            cache.set(token, cached)

        if cached.get("tier") != "tier1":
            return JSONResponse(
                status_code=403,
                content={"error": {"message": "tier_not_allowed", "type": "auth_error"}},
            )

        new_headers = [(k, v) for k, v in request.scope["headers"] if k.lower() != b"authorization"]
        request.scope["headers"] = new_headers
        request.state.agent_id = cached["agent_id"]
        request.state.agent_tier = cached["tier"]
        request.scope.setdefault("state", {})["agent_id"] = cached["agent_id"]

        return await call_next(request)

    return middleware
