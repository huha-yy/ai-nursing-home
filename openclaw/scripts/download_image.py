#!/usr/bin/env python3
"""
飞书图片下载 —— 从飞书 IM 下载图片消息的原始图片。

用法:
  python download_image.py --message-id om_xxx --image-key xxx

输出:
  下载成功 → 打印保存路径到 stdout（供 feishu-bot.ts 读取）
  下载失败 → 打印 [错误: ...] 到 stderr

环境变量:
  FEISHU_APP_ID     飞书应用 App ID
  FEISHU_APP_SECRET 飞书应用 App Secret
"""

import os
import sys
import time
import argparse
import urllib.request
import urllib.error

from dotenv import load_dotenv

load_dotenv(
    os.path.join(
        os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        ),
        ".env",
    )
)

# Also try environment variables directly (entrypoint wrapper exports them).
if not os.environ.get("FEISHU_APP_ID"):
    for suffix in ("_AGENT1", "_AGENT2", "_AGENT3", "_AGENT4", "_DN1", "_DN2", "_DN3", "_DN4", "_XIAODAI", ""):
        key = os.environ.get(f"FEISHU_APP_ID{suffix}")
        secret = os.environ.get(f"FEISHU_APP_SECRET{suffix}")
        if key and secret:
            os.environ["FEISHU_APP_ID"] = key
            os.environ["FEISHU_APP_SECRET"] = secret
            break

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from feishu_auth import FeishuAuth

FEISHU_BASE = "https://open.feishu.cn/open-apis"
MIN_REQ_INTERVAL = 0.34
_last_request_time = 0.0


def rate_limit():
    global _last_request_time
    now = time.time()
    diff = now - _last_request_time
    if diff < MIN_REQ_INTERVAL:
        time.sleep(MIN_REQ_INTERVAL - diff)
    _last_request_time = time.time()


def download_image(message_id: str, image_key: str, auth: FeishuAuth) -> str | None:
    """从飞书下载图片消息的原始图片，保存到 /tmp/，返回文件路径。"""
    url = f"{FEISHU_BASE}/im/v1/messages/{message_id}/resources/{image_key}?type=image"
    out_path = f"/tmp/image_{image_key}.jpg"

    rate_limit()
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {auth.get_token()}"},
        method="GET",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            with open(out_path, "wb") as f:
                f.write(resp.read())
        file_size = os.path.getsize(out_path)
        if file_size == 0:
            print(f"[错误] 下载图片为空", file=sys.stderr)
            return None
        print(out_path)
        return out_path
    except urllib.error.HTTPError as e:
        print(f"[错误] 下载图片失败: HTTP {e.code}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[错误] 下载图片异常: {e}", file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(description="飞书图片下载")
    parser.add_argument("--message-id", required=True, help="图片消息的 message_id")
    parser.add_argument("--image-key", required=True, help="图片的 image_key")
    args = parser.parse_args()

    try:
        auth = FeishuAuth()
        auth.get_token()
    except RuntimeError as e:
        print(f"[错误] 认证失败: {e}", file=sys.stderr)
        sys.exit(1)

    path = download_image(args.message_id, args.image_key, auth)
    if not path:
        sys.exit(1)


if __name__ == "__main__":
    main()
