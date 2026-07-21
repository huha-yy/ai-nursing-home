"""Outbound HTTP client for Feishu credential validation (spec §7.2).

Validates (app_id, app_secret) against Feishu's tenant_access_token endpoint.
Uses httpx.AsyncClient with explicit timeouts. No retries — admin re-clicks
Save explicitly on failure.
"""

from __future__ import annotations

import httpx


class FeishuValidationError(Exception):
    """Feishu rejected the credentials or the API was unreachable."""

    def __init__(self, message: str, *, feishu_code: int | None = None):
        super().__init__(message)
        self.feishu_code = feishu_code


async def validate_feishu_credentials(
    app_id: str,
    app_secret: str,
    *,
    base_url: str = "https://open.feishu.cn",
) -> None:
    """POST to tenant_access_token/internal; raise on any failure.

    Timeouts: 5 s connect, 10 s total. No retry — the admin clicks Save again.
    """
    url = f"{base_url}/open-apis/auth/v3/tenant_access_token/internal"
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(5.0, connect=5.0),
    ) as client:
        try:
            resp = await client.post(
                url,
                json={"app_id": app_id, "app_secret": app_secret},
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
        except httpx.TimeoutException:
            raise FeishuValidationError(
                "Feishu validation timed out — check network connectivity and retry"
            ) from None
        except httpx.RequestError as exc:
            raise FeishuValidationError(f"Feishu API unreachable: {exc}") from exc

    if resp.status_code != 200:
        raise FeishuValidationError(f"Feishu API returned HTTP {resp.status_code}")

    try:
        body = resp.json()
    except Exception:
        raise FeishuValidationError("Feishu returned non-JSON response") from None

    code = body.get("code")
    if code != 0:
        msg = body.get("msg", "unknown error")
        raise FeishuValidationError(msg, feishu_code=code)
