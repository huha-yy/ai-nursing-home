#!/usr/bin/env python3
"""
vision-ocr handler — 图片文字识别（OCR）与 AI 视觉理解。

提供两个函数：
  ocr(image_path: str) -> str
      用 easyocr 提取图片中的文字，返回提取到的文本。

  analyze(image_path: str, question: str = "") -> str
      用 LLM Vision API 深度分析图片内容。question 可选，默认"描述这张图片"。

用法（通过 process 工具）:
  python3 -c "import sys; sys.path.insert(0,'/opt/openclaw/skills/custom/vision-ocr'); from handler import ocr; print(ocr('/tmp/image.jpg'))"

  python3 -c "import sys; sys.path.insert(0,'/opt/openclaw/skills/custom/vision-ocr'); from handler import analyze; print(analyze('/tmp/image.jpg', '这张图里有什么产品？'))"
"""

import os
import sys
import json
import base64
import urllib.request
import urllib.error

from dotenv import load_dotenv


def _ensure_env():
    """重新加载 .env 文件，确保容器运行时新增的环境变量能被读到。

    OpenClaw agent 容器的 entrypoint 会在启动时 source /app/config/.env，
    但后续新增的变量（如 XIAOMI_MIMO_API_KEY）不会自动进入进程环境。
    这里同时尝试 symlink 路径和 entrypoint 的原始 .env 路径。
    """
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    for _p in (
        os.path.join(_script_dir, "..", "..", ".env"),             # /opt/openclaw/skills/.env (symlink)
        os.path.join(_script_dir, "..", "..", "..", ".env"),        # /opt/openclaw/.env
        "/app/config/.env",                                         # entrypoint source 的原始文件
    ):
        _resolved = os.path.normpath(_p)
        if os.path.isfile(_resolved):
            load_dotenv(_resolved)
            break


_ensure_env()

# ── OCR (easyocr) ──────────────────────────────────────────────

_ocr_reader = None


_ocr_available = True


def _get_reader():
    """延迟初始化 easyocr Reader（首次加载较慢）。
    如果模型下载失败（内网环境），静默降级，返回 None。"""
    global _ocr_reader, _ocr_available
    if not _ocr_available:
        return None
    if _ocr_reader is None:
        try:
            import easyocr
            _ocr_reader = easyocr.Reader(["ch_sim", "en"], gpu=False)
        except ImportError:
            _ocr_available = False
        except Exception:
            # 模型下载失败（内网限制），静默降级
            _ocr_available = False
    return _ocr_reader


def ocr(image_path: str) -> str:
    """提取图片中的文字。

    优先用 easyocr（需联网下载模型），失败时降级到 LLM Vision API。
    """
    if not os.path.isfile(image_path):
        return f"[错误] 文件不存在: {image_path}"

    # 尝试 easyocr
    reader = _get_reader()
    if reader is not None:
        try:
            results = reader.readtext(image_path)
            lines = [text for (_, text, _) in results if text.strip()]
            if lines:
                return "\n".join(lines)
            return "[未检测到文字]"
        except Exception as e:
            # easyocr 失败，降级到 LLM Vision
            pass

    # 降级: 用 LLM Vision API 提取文字
    return analyze(image_path, "请提取这张图片中所有可见的文字，只返回文字内容，不要描述。如果没有文字，返回'[未检测到文字]'。")


# ── LLM Vision API 分析 ────────────────────────────────────────

# 运行时读取，不缓存模块级常量——容器可能在 entrypoint 之后重新写入 .env
# （见 _ensure_env），模块导入时的 env 可能为空。
_DEFAULT_VISION_API_BASE = "https://token-plan-cn.xiaomimimo.com/v1"
_DEFAULT_VISION_MODEL = "mimo-v2.5"


def _vision_api_key() -> str:
    return os.environ.get("XIAOMI_MIMO_API_KEY", "")


def _vision_api_base() -> str:
    return os.environ.get("VISION_API_BASE", _DEFAULT_VISION_API_BASE)


def _vision_model() -> str:
    return os.environ.get("VISION_MODEL", _DEFAULT_VISION_MODEL)


def _image_to_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def analyze(image_path: str, question: str = "") -> str:
    """用 LLM Vision API 深度分析图片内容。"""
    if not os.path.isfile(image_path):
        return f"[错误] 文件不存在: {image_path}"

    _key = _vision_api_key()
    if not _key:
        return "[错误] 未配置 Vision API Key（XIAOMI_MIMO_API_KEY）"

    b64 = _image_to_base64(image_path)
    prompt = question.strip() or "请用中文详细描述这张图片的内容，包括：画面主体、场景、文字、颜色、品牌信息等。"

    body = {
        "model": _vision_model(),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.3,
    }

    req = urllib.request.Request(
        f"{_vision_api_base()}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {_key}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
        return result.get("choices", [{}])[0].get("message", {}).get("content", "[无返回内容]")
    except urllib.error.HTTPError as e:
        return f"[API 错误] HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}"
    except Exception as e:
        return f"[分析错误] {e}"


# ── CLI 入口 ────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="图片 OCR 与视觉理解")
    parser.add_argument("action", choices=["ocr", "analyze"], help="ocr=文字提取, analyze=深度分析")
    parser.add_argument("--image", required=True, help="图片路径")
    parser.add_argument("--question", default="", help="分析问题时使用")
    args = parser.parse_args()

    if args.action == "ocr":
        print(ocr(args.image))
    elif args.action == "analyze":
        print(analyze(args.image, args.question))
