#!/usr/bin/env python3
"""
GBrain → cognee 知识同步脚本。

导出 GBrain 所有页面，写入 cognee 的 company_knowledge 库，
让内容管线能搜索到 GBrain 中的知识。

流程:
  1. docker exec dl-gbrain gbrain export → 临时目录
  2. docker cp 导出文件到宿主机
  3. 遍历 .md 文件，POST /v1/admin/ingest 到 cognee
  4. 清理临时文件

用法:
  python3 sync_gbrain_to_cognee.py

环境变量 (从 .env 自动加载):
  DL_COGNEE_ADMIN_TOKEN   cognee admin 令牌（必需）
  GBRAIN_CONTAINER         GBrain 容器名（默认 dl-gbrain）
  COGNEE_URL               cognee API 地址（默认 http://localhost:8080）
  LIBRARY_SLUG             目标库（默认 company_knowledge）

调度:
  建议每天凌晨 3:15 运行（配合 nightly_gbrain_probe.sh）
  也可手动随时执行
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from pathlib import Path

# ── 日志 ──────────────────────────────────────────────────────────────
LOG_DIR = Path("/home/li/data/dato_prod-main/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "sync_gbrain_to_cognee.log"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("sync")

# ── 配置（含 .env 加载） ───────────────────────────────────────────────
_ENV_PATH = Path("/home/li/data/dato_prod-main/infra/.env")
_ENV_RE = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", re.IGNORECASE)


def _load_dotenv(path: Path) -> None:
    """加载 .env 文件到 os.environ（不覆盖已有变量）。"""
    if not path.exists():
        log.warning(".env 文件不存在: %s", path)
        return
    with open(path) as f:
        for line in f:
            m = _ENV_RE.match(line)
            if m:
                key, val = m.group(1), m.group(2)
                # 去掉引号
                if len(val) > 1 and val[0] == val[-1] and val[0] in ('"', "'"):
                    val = val[1:-1]
                if key not in os.environ:
                    os.environ[key] = val


_load_dotenv(_ENV_PATH)

GBRAIN_CONTAINER = os.environ.get("GBRAIN_CONTAINER", "dl-gbrain")
COGNEE_URL = os.environ.get("COGNEE_URL", "http://localhost:8080")
LIBRARY_SLUG = os.environ.get("LIBRARY_SLUG", "company_knowledge")
COGNEE_PATH_PREFIX = "gbrain"  # cognee 内路径前缀，区别于手动写入

_ADMIN_TOKEN = os.environ.get("DL_COGNEE_ADMIN_TOKEN")
if not _ADMIN_TOKEN:
    log.error("DL_COGNEE_ADMIN_TOKEN 未设置，无法同步")
    sys.exit(1)

# ── 工具函数 ──────────────────────────────────────────────────────────


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """执行命令，返回 CompletedProcess。"""
    log.debug("执行: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _gbrain_export(temp_dir: str) -> bool:
    """在 gbrain 容器内执行 export。

    返回 True 成功，False 失败。
    """
    log.info("导出 GBrain 页面到容器内 %s ...", temp_dir)
    result = _run([
        "docker", "exec", GBRAIN_CONTAINER,
        "gbrain", "export", "--dir", temp_dir,
    ])
    if result.returncode != 0:
        log.error("gbrain export 失败 (exit=%d): %s",
                  result.returncode, result.stderr.strip())
        return False
    log.info("GBrain 导出成功")
    return True


def _cp_from_container(container_dir: str, host_dir: str) -> bool:
    """从容器复制导出文件到宿主机。"""
    log.info("复制导出文件到宿主机 %s ...", host_dir)
    os.makedirs(host_dir, exist_ok=True)
    result = _run([
        "docker", "cp", f"{GBRAIN_CONTAINER}:{container_dir}/.", host_dir,
    ])
    if result.returncode != 0:
        log.error("docker cp 失败 (exit=%d): %s",
                  result.returncode, result.stderr.strip())
        return False
    log.info("文件复制完成")
    return True


def _cleanup_container(container_dir: str) -> None:
    """删除容器内的临时目录。"""
    _run(["docker", "exec", GBRAIN_CONTAINER, "rm", "-rf", container_dir])
    log.debug("容器临时目录已清理: %s", container_dir)


def _ingest_to_cognee(path: str, content: str, retries: int = 3) -> bool:
    """将单个文件写入 cognee。

    参数:
        path — cognee 内路径，如 "gbrain/daien/product/care_robot.md"
        content — 完整文件内容（含 frontmatter）

    返回:
        True 成功, False 失败
    """
    body = json.dumps({
        "library_slug": LIBRARY_SLUG,
        "path": path,
        "content": content,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{COGNEE_URL}/v1/admin/ingest",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_ADMIN_TOKEN}",
        },
        method="POST",
    )

    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()  # 消费响应，触发状态码检查
            return True
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="replace")[:100]
            last_error = f"HTTP {e.code} — {body_text}"
            if e.code in (401, 403):
                # 认证错误，不重试
                log.error("认证失败 (%s)，终止同步", last_error)
                return False
            if attempt < retries:
                wait = attempt * 5
                log.warning("  retry %d/%d: %s (等待 %ds)",
                            attempt, retries, last_error, wait)
                time.sleep(wait)
        except urllib.error.URLError as e:
            last_error = f"连接失败 — {e.reason}"
            if attempt < retries:
                wait = attempt * 5
                log.warning("  retry %d/%d: %s (等待 %ds)",
                            attempt, retries, last_error, wait)
                time.sleep(wait)
        except Exception as e:
            last_error = str(e)
            if attempt < retries:
                wait = attempt * 5
                log.warning("  retry %d/%d: %s (等待 %ds)",
                            attempt, retries, last_error, wait)
                time.sleep(wait)

    log.error("写入失败（重试 %d 次后）: %s — %s", retries, path, last_error)
    return False


# ── 主流程 ────────────────────────────────────────────────────────────


def main():
    log.info("=" * 60)
    log.info("GBrain → cognee 同步开始")

    # 1. 创建临时目录
    timestamp = int(time.time())
    container_dir = f"/tmp/gbrain-sync-{timestamp}"
    host_dir = f"/tmp/gbrain-sync-host-{timestamp}"

    # 2. 导出 GBrain
    if not _gbrain_export(container_dir):
        sys.exit(1)

    # 3. 复制到宿主机
    if not _cp_from_container(container_dir, host_dir):
        _cleanup_container(container_dir)
        sys.exit(1)

    # 4. 清理容器临时目录（尽早清理，内容已复制到宿主机）
    _cleanup_container(container_dir)

    # 5. 遍历文件，写入 cognee
    exported_files = sorted(Path(host_dir).rglob("*.md"))

    if not exported_files:
        log.warning("没有找到 .md 文件，跳过同步")
        shutil.rmtree(host_dir, ignore_errors=True)
        sys.exit(0)

    success_count = 0
    fail_count = 0
    total = len(exported_files)

    log.info("开始同步 %d 个文件到 cognee (%s/%s)...", total, LIBRARY_SLUG, COGNEE_PATH_PREFIX)

    for file_path in exported_files:
        # 计算相对路径：去掉 host_dir 前缀
        rel_path = file_path.relative_to(host_dir)
        # 目标路径：gbrain/<slug>.md
        cognee_path = f"{COGNEE_PATH_PREFIX}/{rel_path}"

        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            log.error("  读取失败 %s — %s", rel_path, e)
            fail_count += 1
            continue

        size = len(content.encode("utf-8"))
        log.info("  [%d/%d] %s (%d bytes) ...",
                 success_count + fail_count + 1, total, cognee_path, size)

        if _ingest_to_cognee(cognee_path, content):
            success_count += 1
            log.info("  ✅ %s", cognee_path)
        else:
            fail_count += 1
            log.error("  ❌ %s", cognee_path)

    # 6. 清理宿主机临时目录
    shutil.rmtree(host_dir, ignore_errors=True)

    # 7. 汇总
    log.info("-" * 40)
    log.info("同步完成: %d/%d 成功, %d 失败", success_count, total, fail_count)

    if fail_count > 0:
        log.warning("有 %d 个文件同步失败，请检查日志", fail_count)
        sys.exit(1)

    log.info("=" * 60)


if __name__ == "__main__":
    main()
