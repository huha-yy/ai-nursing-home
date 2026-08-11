#!/usr/bin/env python3
"""
vision-ocr handler — thin HTTP shim to dl-ocr service.

Sends base64-encoded images to dl-ocr for text extraction and analysis.
Usage (via process tool):
  python3 -c "import sys; sys.path.insert(0,'/opt/openclaw/skills/custom/vision-ocr'); from handler import ocr; print(ocr('/tmp/image.jpg'))"
"""

import base64
import os

import httpx

DL_INTERNAL_TOKEN = os.environ["DL_INTERNAL_TOKEN"]
DL_OCR_URL = os.environ.get("DL_OCR_URL", "http://dl-ocr:8080")
DL_OCR_API_TOKEN = os.environ.get("DL_OCR_API_TOKEN", DL_INTERNAL_TOKEN)


def _client():
    return httpx.Client(
        base_url=DL_OCR_URL,
        headers={"Authorization": f"Bearer {DL_OCR_API_TOKEN}"},
        timeout=httpx.Timeout(5.0, read=60.0),
    )


def ocr(image_path: str) -> str:
    """Extract text from an image file."""
    if not os.path.isfile(image_path):
        return f"[错误] 文件不存在: {image_path}"

    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
    except OSError as exc:
        return f"[错误] 无法读取文件: {exc}"

    try:
        with _client() as c:
            r = c.post("/v1/ocr", json={"image": b64})
            r.raise_for_status()
            data = r.json()
            return data.get("text", "") or "[未检测到文字]"
    except httpx.HTTPStatusError as exc:
        return f"[错误] OCR 服务返回 {exc.response.status_code}"
    except Exception as exc:
        return f"[错误] OCR 请求失败: {exc}"


def analyze(image_path: str, question: str = "") -> str:
    """Analyze an image with OCR.  question is ignored — dl-ocr always
    returns full text extraction.  Kept for backward compatibility."""
    return ocr(image_path)
