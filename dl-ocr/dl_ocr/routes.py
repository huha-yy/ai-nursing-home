"""HTTP routes for dl-ocr."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class OcrRequest(BaseModel):
    image: str  # base64-encoded image


class OcrResponse(BaseModel):
    text: str
    raw_text: str = ""
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    width: int
    height: int


def make_router() -> APIRouter:
    r = APIRouter()

    @r.get("/health")
    async def health(request: Request):
        ready = getattr(request.app.state, "ready", False)
        if not ready:
            return JSONResponse({"status": "starting"}, status_code=503)
        return {"status": "ok"}

    @r.post("/v1/ocr")
    async def ocr_endpoint(request: Request, body: OcrRequest):
        """Run OCR on a base64-encoded image."""
        api_token = getattr(request.app.state, "api_token", "")
        if api_token:
            authorization = request.headers.get("authorization", "")
            expected = f"Bearer {api_token}"
            if not hmac.compare_digest(authorization, expected):
                raise HTTPException(status_code=401, detail="Unauthorized")

        model = getattr(request.app.state, "ocr_model", None)
        if model is None:
            raise HTTPException(status_code=503, detail="OCR model not loaded yet")

        # Browsers sometimes send a complete data URL; internal callers send
        # only the base64 payload.  Reject malformed input instead of letting
        # b64decode silently discard arbitrary characters.
        encoded = body.image
        if encoded.startswith("data:"):
            _, separator, encoded = encoded.partition(",")
            if not separator:
                raise HTTPException(status_code=400, detail="Invalid image data URL")
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="Invalid base64 image") from None
        if not image_bytes:
            raise HTTPException(status_code=400, detail="Empty image")

        # Size check
        max_bytes = getattr(request.app.state, "max_image_bytes", 10 * 1024 * 1024)
        if len(image_bytes) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Image too large (max {max_bytes // 1024 // 1024} MiB)",
            )

        try:
            lock = getattr(request.app.state, "inference_lock", None)
            if lock is None:
                lock = asyncio.Lock()
                request.app.state.inference_lock = lock
            async with lock:
                result = await asyncio.to_thread(model.predict, image_bytes)
            return OcrResponse(
                text=result.get("text", ""),
                raw_text=result.get("raw_text", ""),
                blocks=result.get("blocks") or [],
                width=result["width"],
                height=result["height"],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("OCR inference failed")
            raise HTTPException(status_code=500, detail="OCR inference failed") from exc

    return r
