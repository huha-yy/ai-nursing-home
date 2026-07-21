#!/usr/bin/env python3
"""
MiMo Omni 批量识图 —— 分析参考文章图片，分类打标签，判断复用价值。

用法:
  python analyze_images.py --input samples/reference-articles/wechat-expo-report-jpg/
"""

import os
import sys
import json
import io
import base64
import argparse
import urllib.request
import urllib.error

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

API_KEY = os.environ.get("XIAOMI_MIMO_API_KEY", "")
API_BASE = "https://token-plan-cn.xiaomimimo.com/v1"

PROMPT = """分析这张图片，返回 JSON（不要 markdown 包裹，直接裸 JSON）：

{
  "description_cn": "用中文一句话描述图片内容",
  "category": "产品特写 | 展会全景 | 文字横幅 | 产品卡片 | 品牌尾图 | 展台场景 | 其他",
  "has_product": true或false,
  "has_text_overlay": true或false,
  "has_logo": true或false,
  "pipeline_reusable": true或false,
  "reuse_as": "company_intro_card | product_standard_photo | footer_brand | text_banner | not_reusable",
  "reuse_reason": "一句话说明为什么可以/不可以复用到自动化内容流水线中"
}"""


def image_to_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def analyze_image(image_path):
    """调用 MiMo Omni (OpenAI-compatible) 分析单张图片。"""
    b64 = image_to_base64(image_path)

    body = {
        "model": "mimo-v2.5",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
        "max_tokens": 500,
        "temperature": 0.1,
    }

    req = urllib.request.Request(
        f"{API_BASE}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"]
            # 清理可能的 markdown 包裹
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                if content.endswith("```"):
                    content = content[:-3]
            return json.loads(content)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="MiMo Omni 批量识图")
    parser.add_argument("--input", required=True, help="图片目录路径")
    args = parser.parse_args()

    if not API_KEY:
        print("ERROR: XIAOMI_MIMO_API_KEY 未设置")
        sys.exit(1)

    files = sorted([f for f in os.listdir(args.input) if f.endswith(".jpg")])
    print(f"共 {len(files)} 张图片，调用 MiMo Omni 分析...\n")

    results = []
    for f in files:
        path = os.path.join(args.input, f)
        size_kb = os.path.getsize(path) // 1024
        print(f"  [{f}] ({size_kb}KB) → ", end="", flush=True)
        r = analyze_image(path)
        if "error" in r:
            print(f"ERROR: {r['error']}")
            r["file"] = f
        else:
            print(f"{r.get('category', '?')} → reuse_as={r.get('reuse_as', '?')}")
            r["file"] = f
        results.append(r)

    # 汇总
    print(f"\n{'=' * 60}")
    print("汇总分析\n")
    reusable = [r for r in results if r.get("pipeline_reusable")]
    not_reusable = [r for r in results if not r.get("pipeline_reusable") and "error" not in r]
    errors = [r for r in results if "error" in r]

    print(f"  可复用: {len(reusable)} 张")
    for r in reusable:
        print(f"    {r['file']}: {r.get('reuse_as')} — {r.get('description_cn', '')}")
    print(f"\n  不可复用: {len(not_reusable)} 张")
    for r in not_reusable:
        print(f"    {r['file']}: {r.get('category')} — {r.get('reuse_reason', '')}")
    if errors:
        print(f"\n  错误: {len(errors)} 张")
        for r in errors:
            print(f"    {r['file']}: {r['error']}")

    # 保存详细结果
    out_path = os.path.join(args.input, "image_analysis.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n详细结果保存到: {out_path}")


if __name__ == "__main__":
    main()
