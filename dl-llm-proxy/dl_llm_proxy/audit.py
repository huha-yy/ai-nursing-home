"""P6 — audit POST to dl-control, fired AFTER the response body finishes.

D-P6-20: one metadata-only event per LLM call. For SSE streams we wrap
the ASGI body sender so the audit POST fires after the last chunk is
sent to the client.

D-P6-28: dl-control writes the audit_log row under role=system; the
proxy POSTs to /api/internal/audit (no DB creds at the proxy).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = structlog.get_logger()


class AuditASGIMiddleware:
    """ASGI middleware that captures the FULL response body lifecycle."""

    def __init__(self, app: ASGIApp, *, settings) -> None:
        self.app = app
        self.settings = settings
        self._audit_url = f"{settings.dl_control_url.rstrip('/')}/api/internal/audit"
        self._internal_key = settings.dl_internal_api_key.get_secret_value()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") != "/v1/chat/completions":
            await self.app(scope, receive, send)
            return

        body_chunks: list[bytes] = []

        async def _wrapped_receive() -> Message:
            msg = await receive()
            if msg["type"] == "http.request":
                body_chunks.append(msg.get("body", b""))
            return msg

        started = time.monotonic()
        status_code = 0
        response_bytes = bytearray()

        async def _wrapped_send(msg: Message) -> None:
            nonlocal status_code
            if msg["type"] == "http.response.start":
                status_code = msg["status"]
            elif msg["type"] == "http.response.body" and len(response_bytes) < 64 * 1024:
                response_bytes.extend(msg.get("body", b""))
            await send(msg)

        try:
            await self.app(scope, _wrapped_receive, _wrapped_send)
        finally:
            latency_ms = int((time.monotonic() - started) * 1000)

            agent_id = scope.get("state", {}).get("agent_id") if "state" in scope else None
            if agent_id is not None:
                request_body_bytes = b"".join(body_chunks)
                model = None
                try:
                    import json as _json

                    body_json = _json.loads(request_body_bytes or b"{}")
                    if isinstance(body_json, dict):
                        model = body_json.get("model")
                except Exception:
                    pass

                usage = {}
                try:
                    if response_bytes:
                        import json as _json

                        payload = _json.loads(bytes(response_bytes))
                        if isinstance(payload, dict):
                            usage = payload.get("usage") or {}
                except Exception:
                    pass

                meta: dict[str, Any] = {
                    "service": "dl-llm-local",
                    "model": model,
                    "status_code": status_code,
                    "latency_ms": latency_ms,
                    "rate_limit_decision": ("rejected" if status_code == 429 else "allowed"),
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                    "error": None if status_code < 400 else "upstream_or_proxy_error",
                }
                asyncio.create_task(
                    self._post_audit(
                        {
                            "action": "llm_call",
                            "actor_agent_id": agent_id,
                            "meta": meta,
                        }
                    )
                )

    async def _post_audit(self, payload: dict[str, Any]) -> None:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as c:
                await c.post(
                    self._audit_url,
                    headers={
                        "Authorization": f"Bearer {self._internal_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.RequestError as exc:
            logger.warning("audit_post_failed", error=str(exc))
