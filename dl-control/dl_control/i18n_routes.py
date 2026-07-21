# dl_control/i18n_routes.py  (replaces the Task-2 placeholder)
"""GET /lang/{code} language switch + route-side i18n helpers."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.requests import Request as StarletteRequest

from dl_control import i18n


def _safe_return_path(referer: str | None) -> str:
    """Derive a safe local redirect path from the Referer.

    Drops scheme/host entirely (sidesteps open-redirect and proxy
    scheme-mismatch). Accepts only a single-leading-slash local path;
    anything else → /admin.
    """
    if not referer:
        return "/admin"
    try:
        path = urlsplit(referer).path
    except ValueError:
        return "/admin"
    if path.startswith("/") and not path.startswith("//") and "\\" not in path:
        return path
    return "/admin"


def translator_for(request: StarletteRequest) -> Callable[[str], str]:
    """A `t(key)` bound to the request's cookie language, for route code."""
    return i18n.translator(i18n.normalize_lang(request.cookies.get(i18n.LANG_COOKIE)))


def make_router(*, settings) -> APIRouter:
    r = APIRouter()

    @r.get("/lang/{code}")
    async def set_lang(code: str, request: Request):
        if code not in i18n.LANGS:
            raise HTTPException(status_code=404)
        target = _safe_return_path(request.headers.get("referer"))
        resp = RedirectResponse(url=target, status_code=303)
        resp.set_cookie(
            i18n.LANG_COOKIE,
            code,
            max_age=31_536_000,
            samesite="lax",
            httponly=True,
            secure=True,
            path="/",
        )
        return resp

    return r
