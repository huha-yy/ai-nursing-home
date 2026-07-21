"""dl-control library + verify routes."""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from dl_control.auth.middleware import AuthedRequest, require_admin
from dl_control.auth.sessions import SessionStore
from dl_control.db import Database
from dl_control.settings import Settings as AppSettings


class VerifyRequest(BaseModel):
    token: str


class LibraryInfo(BaseModel):
    slug: str
    access: str
    storage_kind: str


class VerifyResponse(BaseModel):
    agent_id: str
    tier: str
    status: str
    authz_version: int
    libraries: list[dict]


def _read_per_agent_db_password(agents_root: str, agent_id: str) -> str | None:
    """Read per-agent DB password from secrets/per_agent_db/<agent_id>."""
    path = Path(agents_root) / "secrets" / "per_agent_db" / agent_id
    try:
        return path.read_text().strip()
    except (OSError, FileNotFoundError):
        return None


def _read_per_library_db_password(agents_root: str, short8: str) -> str | None:
    """Read per-library DB password from secrets/per_library_db/<short8>."""
    path = Path(agents_root) / "secrets" / "per_library_db" / short8
    try:
        return path.read_text().strip()
    except (OSError, FileNotFoundError):
        return None


async def _verify_token(
    db: Database,
    settings: AppSettings,
    token: str,
) -> VerifyResponse:
    """Verify an agent's internal token and return libraries + DSN."""

    from dl_control.libraries.db_lifecycle import build_library_db_dsn

    token_hash = hashlib.sha256(token.encode()).digest()

    async with db.conn(user_id=None, role="system") as conn:
        cur = await conn.execute(
            "SELECT id, tier, status, cognee_authz_version, "
            "per_agent_db_name, per_agent_db_role "
            "FROM agents WHERE internal_token_hash = %s",
            (token_hash,),
        )
        row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="invalid token")

        agent_id, tier, status, authz_version, per_agent_db_name, per_agent_db_role = row
        agent_id_str = str(agent_id)

        if status != "active":
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "agent_not_active",
                    "status": status,
                },
            )

        # Build libraries list.
        libraries: list[dict] = []

        # Implicit _public (read-only).
        libraries.append({"slug": "_public", "access": "read", "storage_kind": "shared"})

        # ACL-granted libraries.
        cur = await conn.execute(
            "SELECT k.slug, acl.access, k.storage_kind, "
            "k.per_library_db_name, k.per_library_db_role, k.owner_agent_id "
            "FROM agent_library_access acl "
            "JOIN knowledge_libraries k ON acl.library_id = k.id "
            "WHERE acl.agent_id = %s",
            (agent_id_str,),
        )
        for lib_row in await cur.fetchall():
            slug, access, storage_kind, lib_db_name, lib_db_role, owner_agent_id = lib_row
            entry: dict = {"slug": slug, "access": access, "storage_kind": storage_kind}
            if storage_kind == "isolated":
                dsn = None
                agents_root = settings.agents_root
                # Auto-private Tier 1: same DB as agent.
                if owner_agent_id is not None and lib_db_name and lib_db_role:
                    password = _read_per_agent_db_password(agents_root, str(owner_agent_id))
                    if password:
                        dsn = build_library_db_dsn(
                            lib_db_name,
                            lib_db_role,
                            password,
                        )
                    else:
                        # Fallback: try per_library_db secrets.
                        short8 = lib_db_name.removeprefix("cognee_lib_")
                        password = _read_per_library_db_password(agents_root, short8)
                        if password:
                            dsn = build_library_db_dsn(
                                lib_db_name,
                                lib_db_role,
                                password,
                            )
                elif lib_db_name and lib_db_role:
                    # Admin-created isolated.
                    short8 = lib_db_name.removeprefix("cognee_lib_")
                    password = _read_per_library_db_password(agents_root, short8)
                    if password:
                        dsn = build_library_db_dsn(
                            lib_db_name,
                            lib_db_role,
                            password,
                        )
                entry["dsn"] = dsn
            libraries.append(entry)

    return VerifyResponse(
        agent_id=agent_id_str,
        tier=tier,
        status="active",
        authz_version=int(authz_version),
        libraries=libraries,
    )


def _verify_key(auth_header: str | None, settings: AppSettings) -> bool:
    """Check Bearer token matches DL_INTERNAL_API_KEY."""
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    token = auth_header[len("Bearer ") :]
    expected = (
        settings.dl_internal_api_key.get_secret_value() if settings.dl_internal_api_key else None
    )
    if expected is None:
        return False
    return secrets.compare_digest(token, expected)


def make_verify_router(
    db: Database,
    settings: AppSettings,
) -> APIRouter:
    """Router for /api/agent/verify — internal, authenticated by DL_INTERNAL_API_KEY."""

    router = APIRouter()

    @router.post("/api/agent/verify")
    async def verify(request: Request, body: VerifyRequest):
        if not _verify_key(request.headers.get("Authorization"), settings):
            raise HTTPException(status_code=401, detail="unauthorized")
        return await _verify_token(db, settings, body.token)

    return router


# ── Library CRUD Pydantic models ──────────────────────────────────────────


class LibraryCreateRequest(BaseModel):
    slug: str
    display_name: str
    sensitivity: str
    storage_kind: str


class GrantRequest(BaseModel):
    agent_id: str
    access: str


# ── Library CRUD router ───────────────────────────────────────────────────


def make_library_router(
    db: Database,
    sessions: SessionStore,
    settings: AppSettings,
) -> APIRouter:
    """Router for /api/libraries — admin-authenticated CRUD + internal DSN."""

    router = APIRouter()
    admin = require_admin(sessions)

    @router.post("/api/libraries", status_code=201)
    async def create(req: LibraryCreateRequest, authed: AuthedRequest = admin):
        from dl_control.libraries.service import create_library

        owner_dsn = settings.owner_dsn.get_secret_value() if settings.owner_dsn else None
        try:
            lib = await create_library(
                db,
                slug=req.slug,
                display_name=req.display_name,
                sensitivity=req.sensitivity,
                storage_kind=req.storage_kind,
                owner_dsn=owner_dsn,
                actor_user_id=authed.user_id,
            )
        except ValueError as exc:
            msg = str(exc)
            if "invalid slug" in msg:
                raise HTTPException(status_code=400, detail=msg) from exc
            if "already exists" in msg:
                raise HTTPException(status_code=409, detail=msg) from exc
            raise HTTPException(status_code=400, detail=msg) from exc
        return {
            "id": lib.id,
            "slug": lib.slug,
            "display_name": lib.display_name,
            "sensitivity": lib.sensitivity,
            "storage_kind": lib.storage_kind,
            "per_library_db_name": lib.per_library_db_name,
            "per_library_db_role": lib.per_library_db_role,
            "owner_agent_id": lib.owner_agent_id,
            "created_at": lib.created_at,
            "updated_at": lib.updated_at,
        }

    @router.get("/api/libraries")
    async def list_all(authed: AuthedRequest = admin):
        from dl_control.libraries.service import list_libraries

        libs = await list_libraries(db, actor_user_id=authed.user_id)
        return {
            "libraries": [
                {
                    "id": lib.id,
                    "slug": lib.slug,
                    "display_name": lib.display_name,
                    "sensitivity": lib.sensitivity,
                    "storage_kind": lib.storage_kind,
                    "per_library_db_name": lib.per_library_db_name,
                    "per_library_db_role": lib.per_library_db_role,
                    "owner_agent_id": lib.owner_agent_id,
                    "created_at": lib.created_at,
                    "updated_at": lib.updated_at,
                }
                for lib in libs
            ]
        }

    @router.get("/api/libraries/{slug}")
    async def show(slug: str, authed: AuthedRequest = admin):
        from dl_control.libraries.service import get_library

        lib = await get_library(db, slug, actor_user_id=authed.user_id)
        if lib is None:
            raise HTTPException(status_code=404, detail="library not found")
        return {
            "id": lib.id,
            "slug": lib.slug,
            "display_name": lib.display_name,
            "sensitivity": lib.sensitivity,
            "storage_kind": lib.storage_kind,
            "per_library_db_name": lib.per_library_db_name,
            "per_library_db_role": lib.per_library_db_role,
            "owner_agent_id": lib.owner_agent_id,
            "created_at": lib.created_at,
            "updated_at": lib.updated_at,
        }

    @router.delete("/api/libraries/{slug}")
    async def drop(slug: str, authed: AuthedRequest = admin):
        from dl_control.libraries.service import delete_library

        owner_dsn = settings.owner_dsn.get_secret_value() if settings.owner_dsn else None
        cognee_admin_token = (
            settings.dl_cognee_admin_token.get_secret_value()
            if settings.dl_cognee_admin_token
            else None
        )
        try:
            await delete_library(
                db,
                slug,
                owner_dsn=owner_dsn,
                cognee_admin_token=cognee_admin_token,
                cognee_url=settings.dl_cognee_url,
                actor_user_id=authed.user_id,
            )
        except ValueError as exc:
            msg = str(exc)
            if "not found" in msg:
                raise HTTPException(status_code=404, detail=msg) from exc
            if "cannot delete" in msg:
                raise HTTPException(status_code=409, detail=msg) from exc
            raise HTTPException(status_code=400, detail=msg) from exc
        return {"status": "deleted"}

    @router.post("/api/libraries/{slug}/grant")
    async def grant(
        slug: str,
        req: GrantRequest,
        authed: AuthedRequest = admin,
    ):
        from dl_control.libraries.service import grant_access

        try:
            await grant_access(
                db,
                agent_id=req.agent_id,
                library_slug=slug,
                access=req.access,
                actor_user_id=authed.user_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "granted"}

    @router.delete("/api/libraries/{slug}/grants/{agent_id}")
    async def revoke(
        slug: str,
        agent_id: str,
        authed: AuthedRequest = admin,
    ):
        from dl_control.libraries.service import revoke_access

        try:
            await revoke_access(
                db,
                agent_id=agent_id,
                library_slug=slug,
                actor_user_id=authed.user_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "revoked"}

    @router.post("/api/libraries/{slug}/dsn")
    async def dsn(
        slug: str,
        request: Request,
    ):
        if not _verify_key(request.headers.get("Authorization"), settings):
            raise HTTPException(status_code=401, detail="unauthorized")
        from dl_control.libraries.service import get_library_dsn

        try:
            result = await get_library_dsn(
                db,
                slug,
                agents_root=settings.agents_root,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return result

    return router
