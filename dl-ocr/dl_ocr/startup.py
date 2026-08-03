"""Warmup: load the PaddleOCR-VL model and mark the service ready."""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI

from dl_ocr.settings import Settings

logger = logging.getLogger(__name__)


class OcrModel:
    """Wrapper around PaddleOCR-VL for text extraction."""

    def __init__(self, model_dir: str):
        self.model_dir = model_dir
        self._model = None

    def _load(self):
        """Lazy-load the PaddleOCR model."""
        if self._model is not None:
            return

        # Ensure PaddleOCR looks in the mounted volume for models.
        os.environ.setdefault("PADDLEOCR_HOME", self.model_dir)

        # TODO: confirm exact PaddleOCR-VL API when dependency is installed.
        # The model may be loaded via paddleocr.PaddleOCR(...) or via a
        # dedicated PaddleOCR-VL entry point.  Adjust after first build.
        try:
            from paddleocr import PaddleOCR  # type: ignore[import-untyped]

            self._model = PaddleOCR(
                lang="ch",
                use_gpu=True,
                show_log=False,
            )
        except ImportError:
            logger.warning("paddleocr not installed — OCR will be unavailable")
            self._model = None

    def predict(self, image_bytes: bytes) -> dict:
        """Run OCR on an image.  Returns {'text': str, 'blocks': list|None}."""
        self._load()
        if self._model is None:
            return {"text": "", "blocks": None}

        # TODO: replace with actual PaddleOCR-VL inference after confirming API.
        # PaddleOCR-VL may accept bytes, a file path, or a PIL Image.
        result = self._model.ocr(image_bytes, cls=False)
        if result is None or (isinstance(result, list) and len(result) == 0):
            return {"text": "", "blocks": None}

        # Extract text from PaddleOCR's standard output format:
        # [[[bbox], (text, confidence)], ...]
        texts: list[str] = []
        blocks: list[dict] = []
        for page in result:
            if page is None:
                continue
            for item in page:
                bbox, (text, conf) = item
                if text.strip():
                    texts.append(text)
                    blocks.append({"text": text, "confidence": round(conf, 3), "bbox": bbox})

        return {"text": "\n".join(texts), "blocks": blocks or None}


async def warm_up(app: FastAPI, settings: Settings) -> None:
    """Load the OCR model and mark the service ready."""
    logger.info("Loading PaddleOCR-VL model from %s ...", settings.paddleocr_home)
    app.state.ocr_model = OcrModel(model_dir=settings.paddleocr_home)
    app.state.max_image_bytes = settings.max_image_bytes

    # Trigger lazy load now so the first request is fast.
    try:
        app.state.ocr_model._load()
        logger.info("OCR model loaded successfully")
    except Exception:
        logger.exception("OCR model failed to load — service will return 503")

    app.state.ready = True
    logger.info("dl-ocr ready")
