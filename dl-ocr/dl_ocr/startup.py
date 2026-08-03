"""Warmup: load the EasyOCR model and mark the service ready."""

from __future__ import annotations

import logging
import os
from io import BytesIO

import numpy as np
from PIL import Image

from fastapi import FastAPI

from dl_ocr.settings import Settings

logger = logging.getLogger(__name__)


class OcrModel:
    """Wrapper around EasyOCR for text extraction."""

    def __init__(self, model_dir: str):
        self.model_dir = model_dir
        self._model = None

    def _load(self):
        """Lazy-load EasyOCR reader.  Models are cached in *model_dir*."""
        if self._model is not None:
            return

        # Point EasyOCR at the mounted model volume so it reads cached
        # files without downloading.
        if model_dir := self.model_dir:
            os.environ.setdefault("EASYOCR_MODULE_PATH", model_dir)

        try:
            import easyocr  # type: ignore[import-untyped]

            self._model = easyocr.Reader(
                ["ch_sim", "en"],
                gpu=False,               # CPU inference on Jetson
                model_storage_directory=model_dir or None,
                download_enabled=False,  # never download — use cached models
                verbose=False,
            )
            logger.info("EasyOCR reader loaded (ch_sim + en)")
        except ImportError:
            logger.warning("easyocr not installed — OCR unavailable")
            self._model = None
        except Exception:
            logger.exception("EasyOCR init failed")
            self._model = None

    def predict(self, image_bytes: bytes) -> dict:
        """Run OCR on an image.  Returns {'text': str, 'blocks': list|None}."""
        self._load()
        if self._model is None:
            return {"text": "", "blocks": None}

        # Decode image bytes to numpy array (RGB).
        try:
            img = Image.open(BytesIO(image_bytes)).convert("RGB")
            arr = np.array(img)
        except Exception:
            return {"text": "", "blocks": None}

        result = self._model.readtext(arr)
        if not result:
            return {"text": "", "blocks": None}

        texts: list[str] = []
        blocks: list[dict] = []
        for bbox, text, conf in result:
            if text.strip():
                texts.append(text)
                blocks.append({
                    "text": text,
                    "confidence": round(conf, 3),
                    "bbox": [[int(c) for c in pt] for pt in bbox],
                })

        return {"text": "\n".join(texts), "blocks": blocks or None}


async def warm_up(app: FastAPI, settings: Settings) -> None:
    """Load the OCR model and mark the service ready."""
    logger.info("Loading EasyOCR models from %s ...", settings.easyocr_model_dir or "default")
    app.state.ocr_model = OcrModel(model_dir=settings.easyocr_model_dir)
    app.state.max_image_bytes = settings.max_image_bytes

    # Trigger lazy load now so the first request is fast.
    try:
        app.state.ocr_model._load()
    except Exception:
        logger.exception("OCR model failed to load — service returns 503")

    app.state.ready = True
    logger.info("dl-ocr ready")
