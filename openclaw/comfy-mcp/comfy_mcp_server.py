# comfy_mcp_server.py
# ComfyUI 文生图 MCP server —— 带放大重绘,hd 可选
#   hd=True (默认): 完整链路,1024 生成 -> 1536 放大 -> 重绘精细化(高质量,较慢 2-4 分钟)
#   hd=False:       只跑第一段 1024 生成(快,40-90 秒)
# 用户已在网页验证完整工作流在 6GB 上不会 OOM。

import json
import copy
import time
import uuid
import os
import re
import urllib.request
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv

# 尝试从常见位置加载 .env（Agent 容器内）
for _env_candidate in (
    "/app/config/.env",
    "/home/node/.openclaw/.env",
    "/opt/openclaw/.env",
):
    if Path(_env_candidate).exists():
        load_dotenv(_env_candidate)
        break

from mcp.server.fastmcp import FastMCP

# ComfyUI 地址: 环境变量优先,兜底本机
COMFY = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")

# 自动定位脚本同目录读取工作流(不依赖当前工作目录)
HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW_PATH = os.path.join(HERE, "workflow_api.json")

with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
    BASE_WORKFLOW = json.load(f)

# OpenClaw 可读的媒体目录（Agent 能从此路径发送图片）
OPENCLAW_MEDIA_DIR = os.path.expanduser("~/.openclaw/workspace/generated_images")
os.makedirs(OPENCLAW_MEDIA_DIR, exist_ok=True)
mcp = FastMCP("comfyui-image")


def has_chinese(text: str) -> bool:
    """检测字符串中是否含有中文字符。"""
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def queue_prompt(workflow: dict) -> str:
    """把工作流提交给 ComfyUI,返回 prompt_id。"""
    payload = json.dumps({"prompt": workflow}).encode()
    req = urllib.request.Request(f"{COMFY}/prompt", data=payload)
    req.add_header("Content-Type", "application/json")
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp["prompt_id"]


def wait_and_get_image(prompt_id: str, timeout: int = 120):
    """轮询 history 直到出图,返回 first image's (filename, subfolder, type)。
    快速模式 hd=False 一般 20-60s，hd=True 给足 120s。

    ComfyUI 跑在宿主机上（通过 COMFYUI_URL 访问），Agent 容器无法
    直接读宿主机文件系统。因此改用 /view API 下载图片到容器本地目录。
    """
    start = time.time()
    while time.time() - start < timeout:
        r = json.loads(
            urllib.request.urlopen(f"{COMFY}/history/{prompt_id}").read()
        )
        if prompt_id in r:
            outputs = r[prompt_id]["outputs"]
            # 收集所有产出图片的节点,取最后一个(放大重绘后的最终图)
            found = None
            for _, out in outputs.items():
                if "images" in out and out["images"]:
                    img = out["images"][-1]
                    found = (img["filename"], img.get("subfolder", ""), img.get("type", "output"))
            if found:
                return found
        time.sleep(2)
    raise TimeoutError("Image generation timed out.")


def download_via_api(filename: str, subfolder: str, output_type: str) -> bytes:
    """通过 ComfyUI /view API 下载图片（不依赖宿主机本地文件系统）。"""
    params = urllib.parse.urlencode({
        "filename": filename,
        "subfolder": subfolder,
        "type": output_type,
    })
    with urllib.request.urlopen(f"{COMFY}/view?{params}") as resp:
        return resp.read()


@mcp.tool()
def generate_image(prompt: str, negative: str = "",
                   width: int = 1024, height: int = 1024,
                   steps: int = 28, hd: bool = False) ->  str:
    """Generate an image from a text description. Call this when the user asks to
    draw, paint, or create an image.

    IMPORTANT: `prompt` and `negative` MUST be in English. This model only works
    well with English prompts; Chinese input severely degrades quality. If the
    user describes what they want in Chinese, you MUST translate it into English
    first, and add suitable quality tags (e.g. masterpiece, best quality,
    detailed, 8k, photorealistic).

    SPEED NOTE: The model is heavy and runs on a shared GPU. Keep it fast:
    - Default (hd=False): ~20-60s per image, sufficient for PPT illustrations
    - hd=True: ~2-4 min, only use for hero/cover images

    Args:
        prompt: English positive prompt describing content, subject, style,
            lighting, and quality. Example: user says "戴帽子的猫" ->
            "a cat wearing a hat, detailed, photorealistic, 8k".
        negative: English negative prompt listing things to avoid. Leave empty
            to use the built-in default negative prompt.
        width: Base image width in pixels. Default 1024. Use 896 for portrait.
        height: Base image height in pixels. Default 1024. Use 1152 for portrait.
        steps: Sampling steps for the base pass. Default 28.
        hd: If True, run the full pipeline with upscaling and refine
            for higher quality (slower, ~2-4 min). If False (default), only run
            the base 1024 pass (fast, ~20-60s). Use False for most cases.

    Returns:
        The local file path of the generated image.
    """
    # 兜底:检测到中文直接返回提示,让 agent 翻译成英文后重试
    if has_chinese(prompt):
        return ("Error: `prompt` contains Chinese characters. This model only "
                "supports English prompts. Please translate the description into "
                "English and call again, adding English quality tags.")

    wf = copy.deepcopy(BASE_WORKFLOW)

    # ---- 提示词与基础参数(两种模式都用)----
    wf["6"]["inputs"]["text"] = prompt                     # 正向(节点 6)
    if negative.strip():
        wf["7"]["inputs"]["text"] = negative               # 负向(节点 7),留空用默认
    wf["5"]["inputs"]["width"] = width                     # 基础尺寸(节点 5)
    wf["5"]["inputs"]["height"] = height
    wf["3"]["inputs"]["seed"] = uuid.uuid4().int % (2 ** 32)  # 第一段随机种子(节点 3)
    wf["3"]["inputs"]["steps"] = steps

    if hd:
        # ---- 完整链路:修正第二段重绘参数 ----
        wf["23"]["inputs"]["width"] = 1280     # 放大目标从 1536 降到 1280(省显存,更保险)
        wf["23"]["inputs"]["height"] = 1280
        wf["26"]["inputs"]["seed"] = uuid.uuid4().int % (2 ** 32)  # 第二段随机种子
        wf["26"]["inputs"]["denoise"] = 0.35   # 关键:原工作流是 1(等于重画),改 0.35 才是"精细化"
        # 说明:第二段步数保持工作流原值;若想更快,可在此加 wf["26"]["inputs"]["steps"] = 12
        # SaveImage(节点 9)默认已连节点 27(放大重绘后的最终图),不用改
    else:
        # ---- 快速模式:砍掉放大重绘链,直接存第一段的图(节点 8)----
        for dead in ["20", "22", "23", "24", "25", "26", "27"]:
            wf.pop(dead, None)
        wf["9"]["inputs"]["images"] = ["8", 0]

    prompt_id = queue_prompt(wf)
    fn, sub, out_type = wait_and_get_image(prompt_id)
    # 通过 /view API 下载（ComfyUI 在宿主机，容器内没有本地文件系统访问）
    image_data = download_via_api(fn, sub, out_type)
    ext = os.path.splitext(fn)[1] or ".png"
    target_name = f"comfy_{prompt_id}_{uuid.uuid4().hex[:8]}{ext}"
    target_path = os.path.join(OPENCLAW_MEDIA_DIR, target_name)
    with open(target_path, "wb") as f:
        f.write(image_data)

    return  ("Image generate successfully.\n" f"Local media path: {target_path}\n" "Please send/display this file to the user as an image attachment")

if __name__ == "__main__":
    mcp.run()  # 默认 stdio 传输,供 openclaw 拉起
