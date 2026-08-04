"""Warmup: load EasyOCR and mark the service ready."""

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
    """Wrapper around EasyOCR for text extraction (Chinese + English)."""

    def __init__(self, model_dir: str):
        self.model_dir = model_dir
        self._model = None

    def _load(self):
        if self._model is not None:
            return
        try:
            import easyocr
            os.environ.setdefault("HOME", "/tmp")
            self._model = easyocr.Reader(
                ["ch_sim", "en"],
                gpu=False,
                model_storage_directory="/tmp/.EasyOCR",
                download_enabled=True,
                verbose=False,
            )
            logger.info("EasyOCR loaded (ch_sim + en)")
        except ImportError:
            logger.warning("easyocr not installed")
            self._model = None

    def predict(self, image_bytes: bytes) -> dict:
        self._load()
        if self._model is None:
            return {"text": "", "blocks": None}
        try:
            img = Image.open(BytesIO(image_bytes)).convert("RGB")
            arr = np.array(img)
        except Exception:
            return {"text": "", "blocks": None}

        result = self._model.readtext(arr)
        if not result:
            return {"text": "", "blocks": None}

        texts = []
        blocks = []
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
    logger.info("Loading EasyOCR from %s ...", settings.model_dir or "/tmp")
    app.state.ocr_model = OcrModel(model_dir=settings.model_dir)
    app.state.max_image_bytes = settings.max_image_bytes
    try:
        app.state.ocr_model._load()
        app.state.ready = True
        logger.info("dl-ocr ready")
    except Exception:
        logger.exception("EasyOCR failed to load")
        app.state.ready = False
