"""HTTP routes for dl-ocr."""

from __future__ import annotations

import base64
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class OcrRequest(BaseModel):
    image: str  # base64-encoded image


class OcrResponse(BaseModel):
    text: str
    blocks: list[dict[str, Any]] | None = None


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
        model = getattr(request.app.state, "ocr_model", None)
        if model is None:
            raise HTTPException(status_code=503, detail="OCR model not loaded yet")

        # Decode base64 image
        try:
            image_bytes = base64.b64decode(body.image)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 image") from None

        # Size check
        max_bytes = getattr(request.app.state, "max_image_bytes", 10 * 1024 * 1024)
        if len(image_bytes) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Image too large (max {max_bytes // 1024 // 1024} MiB)",
            )

        try:
            result = model.predict(image_bytes)
            return OcrResponse(
                text=result.get("text", ""),
                blocks=result.get("blocks"),
            )
        except Exception as exc:
            logger.exception("OCR inference failed")
            raise HTTPException(status_code=500, detail=f"OCR failed: {exc}") from exc

    return r
