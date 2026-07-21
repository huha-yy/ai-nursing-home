"""Auth dependencies: login/admin gates, CSRF, password-rotation gate, nursing context.

FastAPI dependencies (not Starlette middleware) — composable per route.
The session cookie carries the itsdangerous-signed sid; require_login
resolves it, checks the UA fingerprint, and slides the TTL (spec §6.4–6.8).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, Response, status

from dl_control.auth.errors import MustRotatePasswordError
from dl_control.auth.sessions import SessionStore
from dl_control.db import Database

COOKIE_NAME = "dato_session"


def ua_fingerprint(user_agent: str | None) -> str:
    """A coarse, stable SHA-256 fingerprint of the User-Agent header."""
    return hashlib.sha256((user_agent or "").encode("utf-8")).hexdigest()[:32]


def set_session_cookie(response: Response, *, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


@dataclass(frozen=True, slots=True)
class AuthedRequest:
    user_id: str
    role: str
    sid: str
    csrf_token: str


def require_login(store: SessionStore):
    """Dependency: resolve the signed cookie to a live session, check the
    UA fingerprint, slide the TTL."""

    async def dep(request: Request) -> AuthedRequest:
        raw = request.cookies.get(COOKIE_NAME, "")
        sid = store.unsign(raw) if raw else None
        sess = await store.load(sid) if sid else None
        if sess is None:
            raise HTTPException(status_code=302, headers={"location": "/login"})
        if ua_fingerprint(request.headers.get("user-agent")) != sess.ua_fingerprint:
            await store.delete(sess.sid)
            raise HTTPException(status_code=302, headers={"location": "/login"})
        await store.renew(sess.sid)
        return AuthedRequest(
            user_id=sess.user_id,
            role=sess.role,
            sid=sess.sid,
            csrf_token=sess.csrf_token,
        )

    return Depends(dep)


def require_admin(store: SessionStore):
    """Dependency: require_login plus role == 'admin'."""

    login = require_login(store)

    async def dep(authed: AuthedRequest = login) -> AuthedRequest:
        if authed.role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return authed

    return Depends(dep)


def _host_of(url: str | None) -> str | None:
    return urlsplit(url).hostname if url else None


def require_csrf(store: SessionStore, *, site_host: str):
    """Dependency for state-changing cookie-authed routes (spec §6.7).
    Checks Origin/Referer then the per-session CSRF token. GET is a no-op."""

    async def dep(request: Request) -> None:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return
        host = _host_of(request.headers.get("origin")) or _host_of(request.headers.get("referer"))
        if host is None or (host != site_host and host not in ("localhost", "127.0.0.1")):
            raise HTTPException(status_code=403, detail="origin check failed")
        raw = request.cookies.get(COOKIE_NAME, "")
        sid = store.unsign(raw) if raw else None
        sess = await store.load(sid) if sid else None
        if sess is None:
            raise HTTPException(status_code=403, detail="no session for csrf check")
        supplied = request.headers.get("x-csrf-token")
        if supplied is None:
            form = await request.form()
            supplied = form.get("csrf_token")
        if not supplied or not secrets.compare_digest(str(supplied), sess.csrf_token):
            raise HTTPException(status_code=403, detail="csrf token mismatch")

    return Depends(dep)


def require_password_rotated(*, db: Database, store: SessionStore):
    """App-wide dependency: an admin with must_change_password is redirected
    to /admin/password-change (HTML) or blocked 423 (JSON API)."""

    whitelist = {
        "/",
        "/login",
        "/logout",
        "/admin/password-change",
        "/api/health",
        "/favicon.ico",
    }
    whitelist_prefixes = ("/static/", "/lang/")

    async def dep(request: Request) -> None:
        path = request.url.path
        if path in whitelist or path.startswith(whitelist_prefixes):
            return
        raw = request.cookies.get(COOKIE_NAME, "")
        sid = store.unsign(raw) if raw else None
        sess = await store.load(sid) if sid else None
        if sess is None:
            return
        if sess.role != "admin":
            return
        async with db.conn(user_id=None, role="system") as conn:
            cur = await conn.execute(
                "SELECT must_change_password FROM users WHERE id = %s",
                (sess.user_id,),
            )
            row = await cur.fetchone()
        if not (row and row[0]):
            return
        if path.startswith("/api/v1/admin/"):
            raise MustRotatePasswordError("password change required first")
        raise HTTPException(
            status_code=303 if request.method == "POST" else 302,
            headers={"location": "/admin/password-change"},
        )

    return Depends(dep)


# ---------------------------------------------------------------------------
# Nursing context injection (Task 3)
# ---------------------------------------------------------------------------

# Recognised nursing roles that carry building/floor/dept context.
_NURSING_ROLES = frozenset(
    {"director", "nursing_dept", "logistics_dept", "building", "floor", "general"}
)


@dataclass
class NursingContext:
    """Context extracted from a nursing session, attached to request.state."""

    user_id: str | None = None
    username: str | None = None
    name: str | None = None
    role: str | None = None
    dept: str | None = None
    building: str | None = None
    floor: str | None = None
    is_nursing: bool = False


def inject_nursing_context(store: SessionStore):
    """Dependency: if the current session is a nursing session, extract
    role / dept / building / floor and attach them to request.state as
    request.state.nursing_context. Non-nursing sessions get an empty context."""

    async def dep(request: Request) -> NursingContext:
        raw = request.cookies.get(COOKIE_NAME, "")
        sid = store.unsign(raw) if raw else None
        sess = await store.load(sid) if sid else None
        ctx = NursingContext()
        if sess is None:
            request.state.nursing_context = ctx
            return ctx
        if sess.role not in _NURSING_ROLES:
            request.state.nursing_context = ctx
            return ctx
        ctx.user_id = sess.user_id
        ctx.username = sess.username
        ctx.name = sess.name
        ctx.role = sess.role
        ctx.dept = sess.dept
        ctx.building = sess.building
        ctx.floor = sess.floor
        ctx.is_nursing = True
        request.state.nursing_context = ctx
        return ctx

    return Depends(dep)
