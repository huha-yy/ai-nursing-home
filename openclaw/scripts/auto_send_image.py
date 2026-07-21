#!/usr/bin/env python3
"""
/tmp/ 图片监控发送器 —— 当进程在 /tmp/ 创建新的 jpg/png 文件时，
自动通过 send_image.py 发送到飞书的指定会话。

原理：用 inotify 或轮询监控 /tmp/ 下的新图片文件。
模型（或其他进程）只需把图片下载到 /tmp/xxx.jpg，
此脚本自动用 send_image.py 发出。

用法：作为 sidecar 在 entrypoint 中启动。
配置通过环境变量：
  AUTO_SEND_CHAT_ID — 目标 chat_id（必需）
  AUTO_SEND_CHAT_TYPE — open_id 或 chat_id（默认 chat_id）
  FEISHU_APP_ID / FEISHU_APP_SECRET — 飞书凭据
"""

import os
import sys
import time
import subprocess
import hashlib

SCRIPTS_DIR = "/opt/openclaw/skills/custom/feishu-publisher/scripts"
SEND_IMAGE = os.path.join(SCRIPTS_DIR, "send_image.py")

CHAT_ID = os.environ.get("AUTO_SEND_CHAT_ID", "")
CHAT_TYPE = os.environ.get("AUTO_SEND_CHAT_TYPE", "chat_id")
POLL_INTERVAL = 2  # seconds
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def get_image_hashes(path: str) -> set:
    """Return set of (filename, sha256[:16]) for all images in path."""
    seen = set()
    if not os.path.isdir(path):
        return seen
    try:
        for fname in os.listdir(path):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in IMAGE_EXTS:
                continue
            fpath = os.path.join(path, fname)
            if not os.path.isfile(fpath):
                continue
            # Quick hash to detect duplicates
            with open(fpath, "rb") as f:
                h = hashlib.sha256(f.read(65536)).hexdigest()[:16]
            seen.add((fname, h))
    except PermissionError:
        pass
    return seen


def send_image(filepath: str):
    """Send image via send_image.py."""
    cmd = [
        sys.executable, SEND_IMAGE,
        "--file", filepath,
        "--to", CHAT_ID,
        "--type", CHAT_TYPE,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    for line in result.stdout.splitlines():
        print(f"[auto-send] {line.strip()}")
    for line in result.stderr.splitlines():
        print(f"[auto-send] ERR: {line.strip()}")
    return result.returncode == 0


def main():
    if not CHAT_ID:
        print("[auto-send] AUTO_SEND_CHAT_ID not set — exiting", flush=True)
        return

    if not os.path.isfile(SEND_IMAGE):
        print(f"[auto-send] {SEND_IMAGE} not found — exiting", flush=True)
        return

    print(f"[auto-send] watching /tmp/ for images → {CHAT_ID} ({CHAT_TYPE})", flush=True)
    known = get_image_hashes("/tmp/")

    while True:
        time.sleep(POLL_INTERVAL)
        current = get_image_hashes("/tmp/")
        new_files = current - known
        if new_files:
            for fname, _ in sorted(new_files):
                fpath = os.path.join("/tmp/", fname)
                print(f"[auto-send] new image: {fpath}", flush=True)
                # Small delay to let the write finish
                time.sleep(1)
                send_image(fpath)
        known = current


if __name__ == "__main__":
    main()
