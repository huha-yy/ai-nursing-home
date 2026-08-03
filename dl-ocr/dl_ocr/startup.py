"""Warmup: load the HunyuanOCR model and mark the service ready."""

from __future__ import annotations

import logging
import os
from io import BytesIO

from PIL import Image

from fastapi import FastAPI

from dl_ocr.settings import Settings

logger = logging.getLogger(__name__)


class OcrModel:
    """Wrapper around tencent/HunyuanOCR-1.5 for text extraction.

    Loaded via ``transformers`` with ``trust_remote_code=True`` (the
    HunYuanVL architecture is not yet in the main transformers branch)."""

    def __init__(self, model_name: str, model_dir: str):
        self.model_name = model_name
        self.model_dir = model_dir
        self._model = None
        self._processor = None

    def _load(self):
        """Lazy-load the HunyuanOCR model + processor from the cached volume."""
        if self._model is not None:
            return

        # Point HF at the mounted model volume for offline cache reads.
        if model_dir := self.model_dir:
            os.environ.setdefault("HF_HOME", model_dir)

        try:
            import torch
            from transformers import AutoModel, AutoProcessor  # type: ignore[import-untyped]

            logger.info("Loading HunyuanOCR from %s ...", self.model_name)
            self._processor = AutoProcessor.from_pretrained(
                self.model_name,
                trust_remote_code=True,
                cache_dir=model_dir or None,
                local_files_only=bool(model_dir),
            )
            self._model = AutoModel.from_pretrained(
                self.model_name,
                trust_remote_code=True,
                torch_dtype=torch.bfloat16,
                cache_dir=model_dir or None,
                local_files_only=bool(model_dir),
            )
            if torch.cuda.is_available():
                self._model = self._model.cuda()
            self._model.eval()
            logger.info("HunyuanOCR loaded successfully (device=%s)",
                "cuda" if torch.cuda.is_available() else "cpu")
        except ImportError as exc:
            logger.warning("transformers/torch not installed — OCR unavailable: %s", exc)
            self._model = None
        except Exception:
            logger.exception("HunyuanOCR init failed")
            self._model = None

    def predict(self, image_bytes: bytes) -> dict:
        """Run OCR on an image.  Returns {'text': str, 'blocks': list|None}."""
        self._load()
        if self._model is None:
            return {"text": "", "blocks": None}

        try:
            img = Image.open(BytesIO(image_bytes)).convert("RGB")
        except Exception:
            return {"text": "", "blocks": None}

        # HunyuanOCR prompt for text extraction.
        messages = [
            {"role": "user", "content": [
                {"type": "image", "image": img},
                {"type": "text", "text": "请提取图片中所有文字，按阅读顺序输出。"},
            ]}
        ]
        try:
            prompt = self._processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            inputs = self._processor(
                text=prompt, images=img, return_tensors="pt"
            )
            if self._model.device.type == "cuda":
                inputs = {k: v.cuda() for k, v in inputs.items()}
            import torch
            with torch.no_grad():
                outputs = self._model.generate(**inputs, max_new_tokens=2048)
            text = self._processor.decode(outputs[0], skip_special_tokens=True)
            return {"text": text.strip(), "blocks": None}
        except Exception:
            logger.exception("HunyuanOCR inference failed")
            return {"text": "", "blocks": None}


async def warm_up(app: FastAPI, settings: Settings) -> None:
    """Load the OCR model and mark the service ready."""
    logger.info("Loading HunyuanOCR model from %s ...", settings.model_dir or "HuggingFace hub")
    app.state.ocr_model = OcrModel(
        model_name=settings.model_name,
        model_dir=settings.model_dir,
    )
    app.state.max_image_bytes = settings.max_image_bytes

    try:
        app.state.ocr_model._load()
    except Exception:
        logger.exception("OCR model failed to load — service returns 503")

    app.state.ready = True
    logger.info("dl-ocr ready")
