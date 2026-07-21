#!/usr/bin/env python3
"""
飞书音频转写 —— 下载飞书语音消息，用 Whisper 转写为文字。

用法:
  python transcribe_audio.py --message-id om_xxx --file-key xxx

流程:
  1. 通过飞书 API 下载音频文件
  2. 用 whisper CLI 转写为中文文字
  3. 输出转写结果到 stdout

环境变量:
  FEISHU_APP_ID     飞书应用 App ID
  FEISHU_APP_SECRET 飞书应用 App Secret
"""

import os
import sys
import json
import time
import argparse
import subprocess
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

# 频率控制 (3 req/s)
MIN_REQ_INTERVAL = 0.34
_last_request_time = 0.0


def rate_limit():
    global _last_request_time
    now = time.time()
    diff = now - _last_request_time
    if diff < MIN_REQ_INTERVAL:
        time.sleep(MIN_REQ_INTERVAL - diff)
    _last_request_time = time.time()


def download_audio(message_id: str, file_key: str, auth: FeishuAuth) -> str | None:
    """从飞书下载语音消息的音频文件，保存到 /tmp/，返回文件路径。"""
    url = f"{FEISHU_BASE}/im/v1/messages/{message_id}/resources/{file_key}?type=file"
    out_path = f"/tmp/audio_{message_id}.ogg"

    rate_limit()
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {auth.get_token()}",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            with open(out_path, "wb") as f:
                f.write(resp.read())
        file_size = os.path.getsize(out_path)
        if file_size == 0:
            print(f"下载失败: 空文件", file=sys.stderr)
            return None
        print(f"下载音频: {out_path} ({file_size / 1024:.1f}KB)", file=sys.stderr)
        return out_path
    except urllib.error.HTTPError as e:
        print(f"下载音频失败: HTTP {e.code}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"下载音频异常: {e}", file=sys.stderr)
        return None


def transcribe(audio_path: str) -> str | None:
    """用 whisper CLI 转写音频文件，返回文字。"""
    # whisper outputs a .txt file next to the input by default
    # Use --model base (pre-downloaded), --language Chinese
    result_path = audio_path.rsplit(".", 1)[0] + ".txt"

    cmd = [
        "whisper",
        audio_path,
        "--model", "base",
        "--language", "Chinese",
        "--output_dir", "/tmp",
        "--verbose", "False",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"Whisper 转写失败: {result.stderr[:200]}", file=sys.stderr)
            return None

        # whisper writes the output to a .txt file
        if os.path.isfile(result_path):
            with open(result_path, encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                return text

        # If no .txt file, try parsing stdout
        if result.stdout.strip():
            return result.stdout.strip()

        print("Whisper 转写为空", file=sys.stderr)
        return None

    except subprocess.TimeoutExpired:
        print("Whisper 转写超时 (5分钟)", file=sys.stderr)
        return None
    except FileNotFoundError:
        print("whisper CLI 未找到，请确认已安装", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Whisper 转写异常: {e}", file=sys.stderr)
        return None


def cleanup(audio_path: str):
    """清理临时音频和转写文件。"""
    try:
        if audio_path and os.path.isfile(audio_path):
            os.remove(audio_path)
        txt_path = audio_path.rsplit(".", 1)[0] + ".txt" if audio_path else ""
        if txt_path and os.path.isfile(txt_path):
            os.remove(txt_path)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="飞书语音转写 — 下载飞书语音消息并转写为文字")
    parser.add_argument("--message-id", required=True, help="语音消息的 message_id")
    parser.add_argument("--file-key", required=True, help="语音消息的 file_key（从 content JSON 中提取）")
    args = parser.parse_args()

    # 获取认证
    try:
        auth = FeishuAuth()
        auth.get_token()
    except RuntimeError as e:
        print(f"❌ 认证失败: {e}", file=sys.stderr)
        sys.exit(1)

    # Step 1: 下载音频
    audio_path = download_audio(args.message_id, args.file_key, auth)
    if not audio_path:
        print("[语音转写失败: 无法下载音频]", file=sys.stderr)
        sys.exit(1)

    # Step 2: 转写
    text = transcribe(audio_path)
    cleanup(audio_path)

    if text:
        # stdout 输出转写结果（LLM 读取）
        print(text)
        sys.exit(0)
    else:
        print("[语音转写失败: 无法转写音频]", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
