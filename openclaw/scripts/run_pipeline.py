#!/usr/bin/env python3
"""
内容管线一键执行脚本 —— 启动 → 轮询 → 输出结果。
Agent Manager 只需执行这一个命令，不需要分步调 API。

用法:
  python3 /opt/openclaw/scripts/run_pipeline.py --brand yonghe
  python3 /opt/openclaw/scripts/run_pipeline.py --brand daien --topic "养老政策新变化"
  python3 /opt/openclaw/scripts/run_pipeline.py --brand yonghe  # 自动选题

环境变量:
  DL_INTERNAL_TOKEN  必填，API 认证
"""

import argparse
import json
import os
import sys
import time

import httpx

DL_CONTROL_URL = os.environ.get("DL_CONTROL_URL", "http://dato-control:8080")
TOKEN = os.environ.get("DL_INTERNAL_TOKEN", "")

if not TOKEN:
    print("❌ 错误：DL_INTERNAL_TOKEN 未设置", file=sys.stderr)
    sys.exit(1)


def _api(method, path, body=None):
    """调用 dl-control 内部 API。"""
    url = f"{DL_CONTROL_URL}{path}"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    r = httpx.Client(base_url=DL_CONTROL_URL, headers=headers, timeout=30.0)
    if method == "GET":
        resp = r.get(path)
    else:
        resp = r.post(path, json=body or {})
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="内容管线一键执行")
    parser.add_argument("--brand", default="daien", help="品牌 slug（daien/yonghe）")
    parser.add_argument("--topic", default=None, help="文章主题（不传则自动热点选题）")
    args = parser.parse_args()

    brand = args.brand.strip().lower()
    topic = args.topic.strip() if args.topic else None

    # 1. 启动管线
    # no_webhook=true: Agent Manager 在对话中已有回复，不需要群通知
    body = {"input": {"brand": brand, "no_webhook": True}}
    if topic:
        body["input"]["topic"] = topic

    print(f"🚀 启动内容管线（品牌={brand}）...")
    if topic:
        print(f"   主题：{topic}")
    else:
        print("   自动选题中...")

    result = _api("POST", "/api/internal/admin/workflows/content.pipeline/start", body)
    run_id = result.get("run_id", "")
    if not run_id:
        print(f"❌ 启动失败：{json.dumps(result, ensure_ascii=False)}", file=sys.stderr)
        sys.exit(1)

    # 2. 轮询（静默 — 不在终端输出中间状态）
    while True:
        time.sleep(10)
        data = _api("GET", f"/api/internal/admin/workflow-runs/{run_id}")
        run = data.get("run", {})
        status = run.get("status", "")
        if status in ("succeeded", "failed", "cancelled"):
            break

    # 3. 取结果
    data = _api("GET", f"/api/internal/admin/workflow-runs/{run_id}")
    steps = data.get("steps", [])

    # 找 feishu-publisher 步骤的输出
    doc_url = ""
    for s in steps:
        if s.get("step_key") == "feishu-publisher" and s.get("status") == "succeeded":
            output = s.get("output", "")
            if isinstance(output, str):
                # 尝试解析 JSON
                try:
                    parsed = json.loads(output)
                    if isinstance(parsed, dict):
                        urls = parsed.get("feishu_urls", {}) or parsed
                        if isinstance(urls, dict):
                            for v in urls.values():
                                if isinstance(v, str) and "feishu" in v:
                                    doc_url = v
                                    break
                            if not doc_url:
                                doc_url = next(iter(urls.values()), "")
                except json.JSONDecodeError:
                    # 纯文本，正则找链接
                    import re
                    m = re.search(r"https?://[^\s]*(?:feishu|doc|docx)[^\s]*", output)
                    if m:
                        doc_url = m.group(0)

    if status == "succeeded" and doc_url:
        print(f"\n✅ 管线执行成功！")
        print(f"\n📄 飞书文档：{doc_url}")
    elif status == "succeeded":
        print(f"\n✅ 管线执行成功，但未找到飞书文档链接。")
        print(f"   最终步骤输出：{data.get('steps', [{}])[-1].get('output', '')[:200]}")
    else:
        print(f"\n❌ 管线执行失败（{status}）")
        for s in steps:
            if s.get("status") == "failed":
                print(f"  步骤 {s['step_key']} 失败：{s.get('error', '')}")


if __name__ == "__main__":
    main()
