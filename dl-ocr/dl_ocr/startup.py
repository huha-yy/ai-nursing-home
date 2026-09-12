"""Warmup and inference helpers for Baidu Unlimited-OCR."""

from __future__ import annotations

import ast
import asyncio
import logging
import os
import re
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from PIL import Image, ImageOps

from dl_ocr.settings import Settings

logger = logging.getLogger(__name__)

# The appliance runs this container with a read-only root filesystem. Triton
# otherwise tries to compile GPU kernels below /root/.triton on first infer.
os.environ.setdefault("TRITON_CACHE_DIR", "/tmp/triton")
# The slim runtime intentionally has no compiler. Disable PyTorch's optional
# native-JIT overrides so eager CUDA kernels are used instead of runtime C
# compilation; this keeps inference compatible with the hardened image.
os.environ.setdefault("TORCH_DISABLE_NATIVE_JIT", "1")

_REF_DET_RE = re.compile(
    r"<\|ref\|>(.*?)<\|/ref\|>\s*<\|det\|>(.*?)<\|/det\|>",
    re.DOTALL,
)
_DET_BLOCK_RE = re.compile(
    r"<\|det\|>([^<\s]+)(?:\s*(\[[^\]]*\]))?\s*<\|/det\|>"
    r"(.*?)(?=(?:\r?\n)?<\|det\|>|\Z)",
    re.DOTALL,
)
_DET_RE = re.compile(r"<\|det\|>.*?<\|/det\|>", re.DOTALL)
_REF_RE = re.compile(r"<\|/?ref\|>")
_EOS_TOKENS = ("<｜end▁of▁sentence｜>", "<|end_of_sentence|>")


def _boxes(raw: str) -> list[list[float]]:
    """Parse model coordinates without evaluating arbitrary model output."""
    try:
        value = ast.literal_eval(raw.strip())
    except (SyntaxError, ValueError):
        return []
    if isinstance(value, (list, tuple)) and len(value) == 4 and all(
        isinstance(item, (int, float)) for item in value
    ):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    result: list[list[float]] = []
    for item in value:
        if isinstance(item, (list, tuple)) and len(item) == 4 and all(
            isinstance(point, (int, float)) for point in item
        ):
            result.append([float(point) for point in item])
    return result


def parse_ocr_output(raw: str, *, width: int, height: int) -> tuple[str, list[dict[str, Any]]]:
    """Return clean text plus provenance blocks from Unlimited-OCR tags."""
    blocks: list[dict[str, Any]] = []

    def replace(match: re.Match[str]) -> str:
        text = match.group(1).strip()
        normalized_boxes = _boxes(match.group(2))
        for box in normalized_boxes:
            x1, y1, x2, y2 = box
            blocks.append(
                {
                    "text": text,
                    "bbox": box,
                    "bbox_pixels": [
                        round(x1 / 999 * width),
                        round(y1 / 999 * height),
                        round(x2 / 999 * width),
                        round(y2 / 999 * height),
                    ],
                }
            )
        return text

    text = _REF_DET_RE.sub(replace, raw)

    def replace_det_block(match: re.Match[str]) -> str:
        category = match.group(1).strip()
        content = match.group(3).strip()
        for box in _boxes(match.group(2) or ""):
            x1, y1, x2, y2 = box
            blocks.append(
                {
                    "text": content,
                    "category": category,
                    "bbox": box,
                    "bbox_pixels": [
                        round(x1 / 999 * width),
                        round(y1 / 999 * height),
                        round(x2 / 999 * width),
                        round(y2 / 999 * height),
                    ],
                }
            )
        return "" if category == "image" else content

    text = _DET_BLOCK_RE.sub(replace_det_block, text)
    text = _DET_RE.sub("", text)
    text = _REF_RE.sub("", text)
    for token in _EOS_TOKENS:
        text = text.replace(token, "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, blocks


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

        import torch
        from transformers import AutoModel, AutoTokenizer  # type: ignore[import-untyped]

        if not torch.cuda.is_available():
            raise RuntimeError("Unlimited-OCR requires CUDA, but the container has no GPU access")

        logger.info("Loading Unlimited-OCR from %s ...", model_dir or self.model_name)
        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_dir or self.model_name,
            trust_remote_code=True,
        )
        self._model = AutoModel.from_pretrained(
            self.model_dir or self.model_name,
            trust_remote_code=True,
            torch_dtype=torch.bfloat16,
        ).cuda()
        self._model.eval()
        logger.info("Unlimited-OCR loaded (device=cuda)")

    def predict(self, image_bytes: bytes) -> dict:
        self._load()
        if self._model is None:
            raise RuntimeError("OCR model is not loaded")

        with Image.open(BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
        width, height = image.size
        if width * height > 40_000_000:
            raise ValueError("decoded image exceeds the 40 megapixel limit")

        with tempfile.TemporaryDirectory(prefix="dl-ocr-") as work_dir:
            work_path = Path(work_dir)
            image_path = work_path / "input.png"
            output_path = work_path / "output"
            image.save(image_path)

            # The upstream infer() implementation requires a non-empty
            # output_path even when save_results=False, and only returns text
            # in eval_mode.  It also uses CUDA unconditionally.
            prompt = "<image>document parsing."
            raw_text = self._model.infer(
                tokenizer=self._tokenizer,
                prompt=prompt,
                image_file=str(image_path),
                output_path=str(output_path),
                eval_mode=True,
                save_results=False,
                base_size=1024,
                image_size=640,
                crop_mode=True,
                max_length=32768,
                no_repeat_ngram_size=35,
                ngram_window=128,
                temperature=0.0,
            )

        if not isinstance(raw_text, str):
            raise RuntimeError("OCR model returned no text")
        text, blocks = parse_ocr_output(raw_text, width=width, height=height)
        return {
            "text": text,
            "raw_text": raw_text,
            "blocks": blocks,
            "width": width,
            "height": height,
        }


async def warm_up(app: FastAPI, settings: Settings) -> None:
    logger.info("Loading Unlimited-OCR from %s ...", settings.model_dir or settings.model_name)
    app.state.ocr_model = OcrModel(
        model_name=settings.model_name,
        model_dir=settings.model_dir,
    )
    app.state.inference_lock = asyncio.Lock()
    app.state.max_image_bytes = settings.max_image_bytes
    app.state.api_token = settings.api_token
    try:
        app.state.ocr_model._load()
    except Exception:
        app.state.ready = False
        logger.exception("OCR model failed to load")
        return
    app.state.ready = True
    logger.info("dl-ocr ready")
