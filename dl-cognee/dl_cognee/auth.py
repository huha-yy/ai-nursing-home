"""Agent token verification via dl-control /api/agent/verify (spec §6.2).

Implements positive + negative caching with TTL (spec §7.3).
"""

from __future__ import annotations

import hashlib
import time

from fastapi import HTTPException, Request


async def verify_agent(request: Request) -> dict:
    """FastAPI dependency: extract Bearer token, verify via dl-control,
    return the verify-response dict. Raises 401/403 on failure."""

    app = request.app
    settings = app.state.settings
    control_client = app.state.control_client
    verify_cache: dict[bytes, tuple[dict, float]] = app.state.verify_cache
    verify_cache_negative: dict[bytes, float] = app.state.verify_cache_negative

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing or invalid Authorization header")

    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="empty token")

    token_hash = hashlib.sha256(token.encode()).digest()
    now = time.monotonic()

    # Check positive cache.
    if token_hash in verify_cache:
        cached_val, cached_at = verify_cache[token_hash]
        if now - cached_at < settings.verify_cache_ttl_seconds:
            return cached_val
        else:
            del verify_cache[token_hash]

    # Check negative cache.
    if token_hash in verify_cache_negative:
        if now - verify_cache_negative[token_hash] < settings.verify_cache_negative_ttl_seconds:
            raise HTTPException(status_code=401, detail="invalid token (cached)")
        else:
            del verify_cache_negative[token_hash]

    try:
        resp = await control_client.post(
            "/api/agent/verify",
            json={"token": token},
            headers={"Authorization": f"Bearer {settings.dl_internal_api_key.get_secret_value()}"},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "dl_control_unreachable"},
        ) from exc

    if resp.status_code == 200:
        data = resp.json()
        # Check authz_version bump.
        if token_hash in verify_cache:
            old_val, _old_at = verify_cache[token_hash]
            if data.get("authz_version", 0) > old_val.get("authz_version", 0):
                verify_cache[token_hash] = (data, now)
        else:
            verify_cache[token_hash] = (data, now)
        return data

    if resp.status_code == 401:
        verify_cache_negative[token_hash] = now
        raise HTTPException(status_code=401, detail="invalid token")

    if resp.status_code == 403:
        raise HTTPException(status_code=403, detail=resp.json())

    raise HTTPException(
        status_code=502,
        detail={"error": "dl_control_error", "status": resp.status_code},
    )


def invalidate_verify_cache(
    app_state,
    library_slug: str,
) -> int:
    """Remove all cached verify entries that reference the given library slug.

    Returns the number of cache entries invalidated.
    """
    verify_cache: dict[bytes, tuple[dict, float]] = app_state.verify_cache
    to_remove: list[bytes] = []

    for token_hash, (val, _at) in verify_cache.items():
        for lib in val.get("libraries", []):
            if lib.get("slug") == library_slug:
                to_remove.append(token_hash)
                break

    for key in to_remove:
        del verify_cache[key]

    return len(to_remove)
