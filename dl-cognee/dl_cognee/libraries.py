"""Library resolution — maps verify-response libraries[] to storage calls.

Includes fan-out search with partial-failure handling and isolated DB pool
management (spec §6.1, §7.3).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field

import psycopg_pool

from dl_cognee.storage.isolated import search_isolated
from dl_cognee.storage.shared import search_shared


@dataclass
class ResolvedLibrary:
    slug: str
    access: str
    storage_kind: str  # "shared" or "isolated"
    dsn: str | None = None  # None for shared; per-library DSN for isolated


@dataclass
class SearchResult:
    library_slug: str
    path: str
    chunk_idx: int
    text: str
    cosine_distance: float


@dataclass
class FanOutResponse:
    results: list[dict]
    partial: bool = False
    errors: list[dict] = field(default_factory=list)


_ISO_POOL_LAST_USED: dict[str, float] = {}


async def resolve_libraries(
    verify_response: dict,
    requested_slugs: list[str] | None,
) -> list[ResolvedLibrary]:
    """Resolve requested library slugs against the verify response.

    If requested_slugs is None, returns all readable libraries.
    Raises HTTPException 403 if any requested slug is not in the readable set.
    """
    from fastapi import HTTPException

    libs = verify_response.get("libraries", [])
    readable: dict[str, dict] = {}
    writable: dict[str, dict] = {}
    for lib in libs:
        slug = lib["slug"]
        readable[slug] = lib
        if lib.get("access") in ("read_write", "write"):
            writable[slug] = lib

    if requested_slugs is None:
        targets = list(readable.values())
    else:
        targets = []
        for slug in requested_slugs:
            if slug not in readable:
                raise HTTPException(
                    status_code=403,
                    detail=f"library {slug!r} not in agent's readable set",
                )
            targets.append(readable[slug])

    resolved: list[ResolvedLibrary] = []
    for lib in targets:
        resolved.append(
            ResolvedLibrary(
                slug=lib["slug"],
                access=lib.get("access", "read"),
                storage_kind=lib.get("storage_kind", "shared"),
                dsn=lib.get("dsn"),
            )
        )
    return resolved


def _get_or_create_iso_pool(
    app,
    dsn: str,
    settings,
) -> psycopg_pool.AsyncConnectionPool:
    """Get or create a connection pool for an isolated DB. Evicts LRU if over limit."""
    iso_pools: dict[str, psycopg_pool.AsyncConnectionPool] = app.state.iso_pools
    if dsn in iso_pools:
        _ISO_POOL_LAST_USED[dsn] = time.monotonic()
        return iso_pools[dsn]

    # Evict LRU if over limit.
    if len(iso_pools) >= settings.iso_pool_max:
        lru_dsn = min(_ISO_POOL_LAST_USED, key=lambda k: _ISO_POOL_LAST_USED.get(k, 0))  # type: ignore[arg-type]
        if lru_dsn in iso_pools:
            _close_pool_sync(iso_pools.pop(lru_dsn))
        _ISO_POOL_LAST_USED.pop(lru_dsn, None)

    pool = psycopg_pool.AsyncConnectionPool(dsn, min_size=1, max_size=5)
    iso_pools[dsn] = pool
    _ISO_POOL_LAST_USED[dsn] = time.monotonic()
    return pool


def _close_pool_sync(pool: psycopg_pool.AsyncConnectionPool) -> None:
    """Schedule pool.close() without blocking. Fire-and-forget."""
    with contextlib.suppress(RuntimeError):
        asyncio.ensure_future(pool.close())


async def fan_out_search(
    app,
    libraries: list[ResolvedLibrary],
    embedding: list[float],
    limit: int,
    settings,
) -> FanOutResponse:
    """Execute search against each library in parallel, merge results, handle
    partial failures."""
    owner_pool = app.state.owner_pool

    async def _search_one(lib: ResolvedLibrary) -> tuple[ResolvedLibrary, list[dict] | Exception]:
        try:
            if lib.storage_kind == "shared":
                rows = await asyncio.wait_for(
                    search_shared(
                        owner_pool,
                        library_slug=lib.slug,
                        embedding=embedding,
                        limit=limit,
                    ),
                    timeout=settings.per_library_timeout_seconds,
                )
            else:
                pool = _get_or_create_iso_pool(app, lib.dsn, settings)
                rows = await asyncio.wait_for(
                    search_isolated(
                        pool,
                        embedding=embedding,
                        limit=limit,
                    ),
                    timeout=settings.per_library_timeout_seconds,
                )
            return lib, rows
        except TimeoutError:
            return lib, TimeoutError(f"{lib.slug}: timeout")
        except Exception as exc:
            return lib, exc

    tasks = [_search_one(lib) for lib in libraries]
    gathered = await asyncio.gather(*tasks, return_exceptions=True)

    all_results: list[SearchResult] = []
    errors: list[dict] = []
    partial = False

    for item in gathered:
        if isinstance(item, Exception):
            partial = True
            errors.append({"error": str(item)})
            continue
        lib, rows = item
        if isinstance(rows, Exception):
            partial = True
            errors.append({"library_slug": lib.slug, "error": str(rows)})
            continue
        for row in rows:
            all_results.append(
                SearchResult(
                    library_slug=lib.slug,
                    path=row["path"],
                    chunk_idx=row["chunk_idx"],
                    text=row["text"],
                    cosine_distance=row["cosine_distance"],
                )
            )

    # Merge: sort by cosine_distance, tie-break on (library_slug, path, chunk_idx).
    all_results.sort(key=lambda r: (r.cosine_distance, r.library_slug, r.path, r.chunk_idx))
    trimmed = all_results[:limit]

    return FanOutResponse(
        results=[
            {
                "library_slug": r.library_slug,
                "path": r.path,
                "chunk_idx": r.chunk_idx,
                "text": r.text,
                "cosine_distance": r.cosine_distance,
            }
            for r in trimmed
        ],
        partial=partial,
        errors=errors,
    )
