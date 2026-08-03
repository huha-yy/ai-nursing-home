"""Warmup: load the Baidu Unlimited-OCR model and mark the service ready."""

from __future__ import annotations

import logging
import os
from io import BytesIO

from PIL import Image

from fastapi import FastAPI

from dl_ocr.settings import Settings

logger = logging.getLogger(__name__)


class OcrModel:
    """Wrapper around baidu/Unlimited-OCR for text extraction.

    Based on DeepSeek-V2 architecture with custom UnlimitedOCR head.
    Loaded via ``transformers`` with ``trust_remote_code=True``."""

    def __init__(self, model_name: str, model_dir: str):
        self.model_name = model_name
        self.model_dir = model_dir
        self._model = None
        self._processor = None

    def _load(self):
        if self._model is not None:
            return

        if model_dir := self.model_dir:
            os.environ.setdefault("HF_HOME", os.path.dirname(model_dir))

        try:
            import torch
            from transformers import AutoModel, AutoTokenizer  # type: ignore[import-untyped]

            logger.info("Loading Unlimited-OCR from %s ...", model_dir or self.model_name)
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_dir or self.model_name,
                trust_remote_code=True,
            )
            self._model = AutoModel.from_pretrained(
                self.model_dir or self.model_name,
                trust_remote_code=True,
                torch_dtype=torch.bfloat16,
            )
            if torch.cuda.is_available():
                self._model = self._model.cuda()
            self._model.eval()
            logger.info("Unlimited-OCR loaded (device=%s)",
                "cuda" if torch.cuda.is_available() else "cpu")
        except ImportError as exc:
            logger.warning("transformers not installed: %s", exc)
            self._model = None
        except Exception:
            logger.exception("Unlimited-OCR init failed")
            self._model = None

    def predict(self, image_bytes: bytes) -> dict:
        self._load()
        if self._model is None:
            return {"text": "", "blocks": None}

        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        try:
            img = Image.open(BytesIO(image_bytes)).convert("RGB")
            img.save(tmp.name)
            tmp.close()

            # Unlimited-OCR uses the infer() method with <image> token
            prompt = "<image>\n请提取图片中所有文字，按阅读顺序输出。"
            text = self._model.infer(
                tokenizer=self._tokenizer,
                prompt=prompt,
                image_file=tmp.name,
                max_length=4096,
                temperature=0.0,
            )
            return {"text": text.strip() if text else "", "blocks": None}
        except Exception:
            logger.exception("Inference failed")
            return {"text": "", "blocks": None}
        finally:
            try: os.unlink(tmp.name)
            except OSError: pass


async def warm_up(app: FastAPI, settings: Settings) -> None:
    logger.info("Loading Unlimited-OCR from %s ...", settings.model_dir or settings.model_name)
    app.state.ocr_model = OcrModel(
        model_name=settings.model_name,
        model_dir=settings.model_dir,
    )
    app.state.max_image_bytes = settings.max_image_bytes
    try:
        app.state.ocr_model._load()
    except Exception:
        logger.exception("OCR model failed to load")
    app.state.ready = True
    logger.info("dl-ocr ready")
