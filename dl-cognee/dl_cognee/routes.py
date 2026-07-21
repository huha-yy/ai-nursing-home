"""dl-cognee routes — /v1/ingest, /v1/search, /v1/admin/ingest, DELETE /v1/library/{slug}."""

from __future__ import annotations

import contextlib
import secrets
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from dl_cognee.auth import invalidate_verify_cache, verify_agent
from dl_cognee.chunker import chunk_text
from dl_cognee.libraries import FanOutResponse, ResolvedLibrary, fan_out_search, resolve_libraries
from dl_cognee.storage.isolated import ingest_isolated
from dl_cognee.storage.shared import ingest_shared


class IngestRequest(BaseModel):
    content: str
    path: str | None = None
    library_slug: str | None = None
    content_type: str = "text/markdown"


class AdminIngestRequest(BaseModel):
    library_slug: str
    path: str
    content: str
    content_type: str = "text/markdown"
    audit_user_id: str | None = None


class SearchRequest(BaseModel):
    query: str
    limit: int = 5
    library_slugs: list[str] | None = None


class OpenAIEmbedRequest(BaseModel):
    """OpenAI-compatible /v1/embeddings request (for GBrain)."""
    input: str | list[str]
    model: str = "BAAI/bge-m3"
    dimensions: int | None = None


def _validate_path(path: str) -> None:
    """Reject dangerous paths: absolute, .. traversal, NUL bytes, /-prefixed."""
    if not path or "\x00" in path:
        raise HTTPException(status_code=400, detail="invalid path")
    if path.startswith("/"):
        raise HTTPException(status_code=400, detail="absolute paths not allowed")
    if ".." in path.split("/"):
        raise HTTPException(status_code=400, detail="path traversal not allowed")


def _check_admin_token(request: Request) -> None:
    """Verify the admin token from the Authorization header."""
    import secrets as _secrets

    settings = request.app.state.settings
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing or invalid admin token")
    token = auth_header.removeprefix("Bearer ").strip()
    expected = settings.dl_cognee_admin_token.get_secret_value()
    if not _secrets.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="invalid admin token")


async def _ingest_pipeline(
    app_state,
    app,
    library: ResolvedLibrary,
    path: str,
    content: str,
    content_type: str,
    source_agent_id: str | None,
) -> dict:
    """Shared ingest pipeline: chunk, embed, route to storage."""
    settings = app_state.settings
    embedder = app_state.embedder

    chunks = chunk_text(
        content,
        content_type,
        target_chars=settings.chunk_target_chars,
        max_chars=settings.chunk_max_chars,
    )
    if not chunks:
        raise HTTPException(status_code=400, detail="content produced no chunks")

    embeddings = embedder.embed_batch(chunks)

    if library.storage_kind == "shared":
        result = await ingest_shared(
            app_state.owner_pool,
            library_slug=library.slug,
            path=path,
            content=content,
            chunks=chunks,
            embeddings=embeddings,
            source_agent_id=source_agent_id,
        )
    else:
        # Get or create isolated pool.
        from dl_cognee.libraries import _get_or_create_iso_pool

        pool = _get_or_create_iso_pool(app, library.dsn, settings)
        safe_agent_id = source_agent_id or "00000000-0000-0000-0000-000000000000"
        result = await ingest_isolated(
            pool,
            path=path,
            content=content,
            chunks=chunks,
            embeddings=embeddings,
            source_agent_id=safe_agent_id,
        )
        result["library_slug"] = library.slug

    return result


def make_router() -> APIRouter:
    router = APIRouter(prefix="/v1")

    @router.post("/ingest")
    async def ingest(request: Request, body: IngestRequest, agent: dict = Depends(verify_agent)):  # noqa: B008
        """Agent-driven ingest (spec §6.1)."""
        app_state = request.app.state
        settings = app_state.settings

        if len(body.content.encode()) > settings.max_content_bytes:
            raise HTTPException(status_code=413, detail="content too large")

        # Resolve library_slug (default = agent's auto-private).
        libs = agent.get("libraries", [])
        writable: dict[str, dict] = {}
        default_lib = None
        for lib in libs:
            if lib.get("access") in ("read_write", "write"):
                writable[lib["slug"]] = lib
                if default_lib is None and lib["slug"] != "_public":
                    default_lib = lib

        target_slug = body.library_slug
        if target_slug is None:
            if default_lib is None:
                raise HTTPException(
                    status_code=403,
                    detail="no writable library available (and no library_slug specified)",
                )
            target_slug = default_lib["slug"]
        elif target_slug not in writable:
            raise HTTPException(
                status_code=403,
                detail=f"no write access to library {target_slug!r}",
            )

        # Validate path.
        path = body.path or f"ingest_{int(time.time())}.md"
        _validate_path(path)

        # Convert to ResolvedLibrary.
        target_info = writable[target_slug]
        library = ResolvedLibrary(
            slug=target_slug,
            access=target_info.get("access", "read_write"),
            storage_kind=target_info.get("storage_kind", "shared"),
            dsn=target_info.get("dsn"),
        )

        result = await _ingest_pipeline(
            app_state,
            request.app,
            library,
            path,
            body.content,
            body.content_type,
            source_agent_id=agent.get("agent_id"),
        )
        return result

    @router.post("/search")
    async def search(request: Request, body: SearchRequest, agent: dict = Depends(verify_agent)):  # noqa: B008
        """Agent-driven search with optional reranker (spec §6.1)."""
        app_state = request.app.state
        settings = app_state.settings
        embedder = app_state.embedder

        if body.limit < 1 or body.limit > settings.max_search_limit:
            raise HTTPException(status_code=400, detail="invalid limit")

        libraries = await resolve_libraries(agent, body.library_slugs)
        if len(libraries) > settings.max_fan_out_libraries:
            raise HTTPException(
                status_code=400,
                detail=f"too many libraries ({len(libraries)} > {settings.max_fan_out_libraries})",
            )

        # Embed query once.
        embeddings = embedder.embed_batch([body.query])
        query_embedding = embeddings[0]

        # Phase 1: Vector search — fetch extra candidates for reranker.
        fetch_limit = body.limit * 3
        if fetch_limit > settings.max_search_limit * 3:
            fetch_limit = settings.max_search_limit * 3

        response: FanOutResponse = await fan_out_search(
            request.app,
            libraries,
            query_embedding,
            fetch_limit,
            settings,
        )

        results = response.results

        # Phase 2: Rerank via dl-cognee-reranker if enabled.
        if settings.reranker_enabled and results:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    candidates = [
                        {
                            "text": r["text"],
                            "library_slug": r["library_slug"],
                            "path": r["path"],
                            "chunk_idx": r["chunk_idx"],
                            "cosine_distance": r["cosine_distance"],
                        }
                        for r in results
                    ]
                    rerank_resp = await client.post(
                        f"{settings.reranker_url}/rerank",
                        json={
                            "query": body.query,
                            "candidates": candidates,
                            "top_k": body.limit,
                        },
                        timeout=10.0,
                    )
                if rerank_resp.status_code == 200:
                    data = rerank_resp.json()
                    results = data["results"]
            except Exception:
                # Fall back to vector-only results on reranker failure.
                results = results[:body.limit]
        else:
            # No reranker; trim to original limit.
            results = results[:body.limit]

        return {
            "results": results,
            "partial": response.partial,
            "errors": response.errors if response.errors else None,
        }

    @router.post("/embeddings")
    async def openai_embed(request: Request, body: OpenAIEmbedRequest):
        """OpenAI-compatible /v1/embeddings endpoint (for GBrain).

        Accepts GBrain's llama-server recipe requests and returns
        OpenAI-format embedding responses. Uses DL_INTERNAL_API_KEY
        for auth (optional — llama-server recipe sends no auth by default).
        """
        app_state = request.app.state
        # Optional auth: verify if DL_INTERNAL_API_KEY is present in request.
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            expected = app_state.settings.dl_internal_api_key.get_secret_value()
            provided = auth_header.removeprefix("Bearer ").strip()
            if not secrets.compare_digest(provided.encode(), expected.encode()):
                raise HTTPException(status_code=401, detail="invalid token")

        texts = [body.input] if isinstance(body.input, str) else body.input
        embeddings = app_state.embedder.embed_batch(texts)
        data = [
            {"object": "embedding", "index": i, "embedding": emb}
            for i, emb in enumerate(embeddings)
        ]
        return {
            "object": "list",
            "data": data,
            "model": body.model,
            "usage": {"prompt_tokens": len("".join(texts)), "total_tokens": len("".join(texts))},
        }

    @router.post("/admin/ingest")
    async def admin_ingest(request: Request, body: AdminIngestRequest):
        """Admin ingest — authenticated by DL_COGNEE_ADMIN_TOKEN (spec §6.1)."""
        _check_admin_token(request)

        app_state = request.app.state
        settings = app_state.settings
        control_client = app_state.control_client

        if len(body.content.encode()) > settings.max_content_bytes:
            raise HTTPException(status_code=413, detail="content too large")

        _validate_path(body.path)

        # Resolve DSN for isolated libraries via dl-control /api/libraries/{slug}/dsn.
        dsn: str | None = None
        storage_kind = "shared"
        try:
            resp = await control_client.post(
                f"/api/libraries/{body.library_slug}/dsn",
                json={"library_slug": body.library_slug},
                headers={
                    "Authorization": f"Bearer {settings.dl_internal_api_key.get_secret_value()}"
                },
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"dl-control unreachable while resolving library DSN: {exc}",
            ) from exc
        if resp.status_code == 200:
            data = resp.json()
            storage_kind = data.get("storage_kind", "shared")
            dsn = data.get("dsn")
        else:
            raise HTTPException(
                status_code=502,
                detail=f"dl-control DSN resolution failed: HTTP {resp.status_code}",
            )

        library = ResolvedLibrary(
            slug=body.library_slug,
            access="read_write",
            storage_kind=storage_kind,
            dsn=dsn,
        )

        result = await _ingest_pipeline(
            app_state,
            request.app,
            library,
            body.path,
            body.content,
            body.content_type,
            source_agent_id=None,
        )
        return result

    @router.delete("/library/{slug}")
    async def delete_library(request: Request, slug: str):
        """Purge library data — authenticated by DL_COGNEE_ADMIN_TOKEN (spec §6.1)."""
        _check_admin_token(request)

        app_state = request.app.state
        settings = app_state.settings
        owner_pool = app_state.owner_pool
        control_client = app_state.control_client

        # Resolve library storage_kind + DSN via dl-control.
        try:
            resp = await control_client.post(
                f"/api/libraries/{slug}/dsn",
                json={"library_slug": slug},
                headers={
                    "Authorization": f"Bearer {settings.dl_internal_api_key.get_secret_value()}"
                },
            )
        except Exception:
            resp = None

        storage_kind = "shared"
        dsn = None
        if resp is not None and resp.status_code == 200:
            data = resp.json()
            storage_kind = data.get("storage_kind", "shared")
            dsn = data.get("dsn")

        if storage_kind == "isolated" and dsn:
            # Connect to isolated DB and purge rows.
            import psycopg

            try:
                conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
                try:
                    await conn.execute("DELETE FROM cognee_iso.chunks")
                    await conn.execute("DELETE FROM cognee_iso.documents")
                finally:
                    await conn.close()
            except Exception:
                pass  # DB may already be gone; dl-control handles the DROP.
        else:
            # Delete from shared tables.
            async with owner_pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM cognee.chunks WHERE library_slug = %s",
                    (slug,),
                )
                await conn.execute(
                    "DELETE FROM cognee.documents WHERE library_slug = %s",
                    (slug,),
                )

        # Close any isolated pool for this library.
        iso_pools: dict = app_state.iso_pools
        if dsn and dsn in iso_pools:
            pool = iso_pools.pop(dsn, None)
            if pool:
                import asyncio as _asyncio

                with contextlib.suppress(RuntimeError):
                    _asyncio.ensure_future(pool.close())

        # Invalidate verify cache for this slug.
        invalidate_verify_cache(app_state, slug)

        return {"deleted": slug}

    return router
