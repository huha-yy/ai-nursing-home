#!/usr/bin/env python3
"""
飞书文件 → 自动解析 → GBrain 入库

接收飞书文件消息，下载文件，用 markitdown 解析为 Markdown，
调用 LLM 自动判断品牌/分类，构造 frontmatter 后写入 GBrain。

用法:
  python3 parse_and_store.py --message-id om_xxx --file-key xxx

输出 (stdout):
  ✅ 已入库到 daien/product/care_robot_v2
  [错误: 原因]

环境变量:
  FEISHU_APP_ID / FEISHU_APP_SECRET  飞书凭证（有后缀 fallback）
  GBRAIN_API_KEY                       GBrain API 密钥
  DEEPSEEK_API_KEY                     用于 LLM 分类的 API 密钥
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        ".env",
    )
)

# ---- 飞书凭证 fallback（同 download_image.py 的模式） ----
if not os.environ.get("FEISHU_APP_ID"):
    for suffix in ("_AGENT3", "_DN3", "_AGENT1", "_DN1", "_AGENT2", "_DN2", "_AGENT4", "_DN4", ""):
        key = os.environ.get(f"FEISHU_APP_ID{suffix}")
        secret = os.environ.get(f"FEISHU_APP_SECRET{suffix}")
        if key and secret:
            os.environ["FEISHU_APP_ID"] = key
            os.environ["FEISHU_APP_SECRET"] = secret
            break

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

# Fallback: feishu_auth.py lives in feishu-publisher's scripts directory
_FEISHU_SCRIPTS = "/opt/openclaw/skills/custom/feishu-publisher/scripts"
if _FEISHU_SCRIPTS not in sys.path:
    sys.path.insert(0, _FEISHU_SCRIPTS)

from feishu_auth import FeishuAuth

# 导入 lint 校验
from lint_frontmatter import load_schema, validate_frontmatter_dict

# 加载 schema 规则（全局缓存）
_LINT_SCHEMA = None


def _get_lint_schema() -> dict | None:
    """延迟加载 schema_rules.yaml，失败不阻断入库。"""
    global _LINT_SCHEMA
    if _LINT_SCHEMA is None:
        schema_path = os.path.join(SCRIPTS_DIR, "schema_rules.yaml")
        try:
            _LINT_SCHEMA = load_schema(schema_path)
        except Exception as e:
            print(f"[警告: 加载 schema 失败 — {e}，跳过 lint 校验]", file=sys.stderr)
            _LINT_SCHEMA = {}
    return _LINT_SCHEMA


def lint_check_frontmatter(frontmatter_str: str) -> list[str] | None:
    """检查 frontmatter 字符串的必填字段完整性。

    参数:
        frontmatter_str — 待检查的完整 frontmatter 字符串

    返回:
        None — 通过或不支持该类型
        [] — 通过（可识别类型且必填齐全）
        ["缺少必填字段: tags", ...] — 不合格，需拒绝入库
    """
    schema = _get_lint_schema()
    if not schema:
        return None  # schema 加载失败，放行

    try:
        import re
        import yaml
        match = re.match(r"^---\s*\n(.*?)\n---", frontmatter_str, re.DOTALL)
        if not match:
            return None
        fm = yaml.safe_load(match.group(1))
        if not isinstance(fm, dict):
            return None
    except Exception:
        return None

    content_type = fm.get("type", "")
    if not content_type or content_type not in schema:
        return None  # 未知类型，放行

    missing_required, _missing_optional, _unknown = validate_frontmatter_dict(
        fm, content_type, schema
    )
    if missing_required:
        return [f"缺少必填字段: {f}" for f in missing_required]
    return []  # 通过


# ---- 常量 ----
FEISHU_BASE = "https://open.feishu.cn/open-apis"
LLM_URL = os.environ.get("DL_LLM_PROXY_URL", "http://dl-llm-proxy:8080/v1/chat/completions")
LLM_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
GBRAIN_URL = os.environ.get("GBRAIN_URL", "http://dl-gbrain:8080")
GBRAIN_API_KEY = os.environ.get("GBRAIN_API_KEY", "")

_MIN_REQ_INTERVAL = 0.34
_last_request_time = 0.0

# ---- 知识库目录结构（供 LLM 分类参考） ----
CATEGORIES = {
    "daien": ["company", "product", "sales", "faq", "training", "operations"],
    "yonghe": ["company", "product", "health", "sales", "faq", "training", "operations"],
    "common": None,  # 允许新建子目录
}

CLASSIFICATION_PROMPT = (
    "你是一个知识库分类助手。根据文件内容判断最适合的分类。\n"
    "\n"
    "品牌（brand）可选值:\n"
    '- "daien" — 戴恩医疗科技（智能护理机器人、助浴设备等）\n'
    '- "yonghe" — 永和大健康/生命优雅（AI脉诊戒指、健康管理）\n'
    '- "common" — 通用/跨品牌知识\n'
    "\n"
    "各品牌下可选目录（category）\n"
    "- daien: company, product, sales, faq, training, operations\n"
    "- yonghe: company, product, health, sales, faq, training, operations\n"
    "- common: 允许新建子目录\n"
    "\n"
    "如果现有目录都不合适，可以新建目录（限在品牌根下）。\n"
    "\n"
    '输出必须是以下纯 JSON 格式（不要 markdown 代码块）：\n'
    '{{"brand": "...", "category": "...", "title": "...", "slug": "..."}}\n'
    "\n"
    "文件标题：{title}\n"
    "文件内容前 2000 字：{content_preview}"
)


def _rate_limit():
    """简单的请求频率限制（同 download_image.py 模式）。"""
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _MIN_REQ_INTERVAL:
        time.sleep(_MIN_REQ_INTERVAL - elapsed)
    _last_request_time = time.time()


def download_file(message_id: str, file_key: str) -> str | None:
    """从飞书下载文件，保存到 /tmp/，返回文件路径。"""
    auth = FeishuAuth()
    token = auth.get_token()

    _rate_limit()
    url = f"{FEISHU_BASE}/im/v1/messages/{message_id}/resources/{file_key}?type=file"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            # 从 Content-Disposition 或 Content-Type 推断扩展名
            content_type = resp.headers.get("Content-Type", "")
            ext = _ext_from_content_type(content_type)
            out_path = f"/tmp/file_{message_id}{ext}"
            with open(out_path, "wb") as f:
                f.write(resp.read())
            return out_path
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[错误: 文件下载失败 HTTP {e.code} — {body}]", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[错误: 文件下载异常 — {e}]", file=sys.stderr)
        return None


def _ext_from_content_type(content_type: str) -> str:
    """根据 Content-Type 推断文件扩展名。"""
    mapping = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "text/plain": ".txt",
        "text/markdown": ".md",
    }
    for ctype, ext in mapping.items():
        if ctype in content_type:
            return ext
    return ".bin"


def parse_with_markitdown(file_path: str) -> str | None:
    """用 markitdown 解析文件为 Markdown。"""
    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(file_path)
        return result.text_content
    except Exception as e:
        print(f"[错误: markitdown 解析失败 — {e}]", file=sys.stderr)
        return None


def classify_content(title: str, content_preview: str) -> dict | None:
    """用 LLM 自动判断品牌/分类/标题/slug。

    返回: {"brand": "daien", "category": "product", "title": "...", "slug": "..."}
    """
    if not LLM_API_KEY:
        print("[错误: DEEPSEEK_API_KEY 未设置，无法调用 LLM 分类]", file=sys.stderr)
        return None

    prompt = CLASSIFICATION_PROMPT.format(
        title=title[:200],
        content_preview=content_preview[:2000],
    )

    body = {
        "model": "deepseek-v4-pro",
        "messages": [
            {
                "role": "system",
                "content": "你是一个知识库分类助手。只输出 JSON，不输出其他内容。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 500,
    }

    req = urllib.request.Request(
        LLM_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LLM_API_KEY}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        raw = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        # 尝试从 markdown 代码块中提取 JSON
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if json_match:
            raw = json_match.group(1).strip()
        result = json.loads(raw)
        # 验证必填字段
        if not all(k in result for k in ("brand", "category", "title", "slug")):
            raise ValueError(f"LLM 返回缺少必填字段: {result}")
        return result
    except Exception as e:
        print(f"[错误: LLM 分类失败 — {e}]", file=sys.stderr)
        return None


def build_slug(result: dict) -> str:
    """根据分类结果构建 slug。"""
    brand = result.get("brand", "common")
    category = result.get("category", "general")
    # slug 中的 title 部分：转小写、去特殊字符、用下划线连接
    raw_title = result.get("slug_title", result.get("title", "untitled"))
    safe = re.sub(r"[^a-zA-Z0-9一-鿿_]", "_", raw_title)
    safe = re.sub(r"_+", "_", safe).strip("_")
    return f"{brand}/{category}/{safe}"


def build_frontmatter(title: str, content_type: str, brand: str = "") -> str:
    """构造合规的 frontmatter 字符串，包含 schema 中定义的全部必填+常用可选字段。

    返回: "---\ntitle: ...\ntype: ...\ntags: \ncreated: ...\n---\n"
    """
    lines = ["---"]
    lines.append(f"title: {title}")
    lines.append(f"type: {content_type}")
    lines.append("tags: ")
    lines.append("---")
    return "\n".join(lines) + "\n"


def write_to_gbrain(slug: str, full_content: str) -> bool:
    """将完整内容（frontmatter + markdown body）写入 GBrain。

    参数:
        slug — GBrain slug，如 "daien/product/care_robot_v2"
        full_content — 带 frontmatter 的完整 markdown

    返回:
        True 写入成功, False 失败
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "put_page",
            "arguments": {
                "slug": slug,
                "content": full_content,
            },
        },
    }

    try:
        import httpx as _httpx
        with _httpx.Client(
            base_url=GBRAIN_URL,
            headers={
                "Authorization": f"Bearer {GBRAIN_API_KEY}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            timeout=_httpx.Timeout(10.0, read=60.0),
        ) as client:
            resp = client.post("/mcp", json=payload)
            resp.raise_for_status()
            # 解析 SSE 响应
            text = resp.text
            for line in text.splitlines():
                if line.startswith("data: "):
                    result = json.loads(line.removeprefix("data: "))
                    err = result.get("error")
                    if err:
                        print(f"[错误: GBrain 写入失败 — {err}]", file=sys.stderr)
                        return False
                    return True
            return True
    except Exception as e:
        print(f"[错误: GBrain 写入异常 — {e}]", file=sys.stderr)
        return False


def parse_frontmatter_title(markdown_content: str) -> str:
    """尝试从已有的 YAML frontmatter 中提取 title。"""
    match = re.match(r"^---\s*\n(.*?)\n---", markdown_content, re.DOTALL)
    if match:
        front = match.group(1)
        title_match = re.search(r"^title:\s*(.+)$", front, re.MULTILINE)
        if title_match:
            return title_match.group(1).strip().strip('"').strip("'")
    return ""


def main():
    parser = argparse.ArgumentParser(description="飞书文件 → 解析 → GBrain 入库")
    parser.add_argument("--message-id", required=True, help="飞书消息 ID")
    parser.add_argument("--file-key", required=True, help="飞书文件 file_key")
    parser.add_argument("--file-path", help="直接指定文件路径（绕过下载，用于调试）")
    args = parser.parse_args()

    # ---- 1. 下载文件 ----
    if args.file_path:
        file_path = args.file_path
    else:
        file_path = download_file(args.message_id, args.file_key)
        if not file_path:
            sys.exit(1)

    # ---- 2. Markitdown 解析 ----
    markdown_content = parse_with_markitdown(file_path)
    if not markdown_content:
        sys.exit(1)

    # ---- 3. 提取标题 + 内容预览 ----
    title = parse_frontmatter_title(markdown_content) or os.path.basename(file_path)
    content_preview = markdown_content[:3000]

    # ---- 4. LLM 自动分类 ----
    classification = classify_content(title, content_preview)
    if not classification:
        sys.exit(1)

    # ---- 5. 构建 slug ----
    slug = build_slug(classification)

    # ---- 6. 构造 frontmatter + lint 校验 ----
    content_type = classification.get("category", "general")
    final_title = classification.get("title", title)
    frontmatter = build_frontmatter(final_title, content_type, brand=classification.get("brand", ""))
    full_content = frontmatter + markdown_content

    lint_errors = lint_check_frontmatter(frontmatter)
    if lint_errors is not None and lint_errors:
        # lint 拒绝 — 必填字段缺失
        for err in lint_errors:
            print(f"[{err}]", file=sys.stderr)
        print(f"[错误: 文件不符合 schema 规则，拒绝入库]", file=sys.stderr)
        sys.exit(1)

    # ---- 7. 写入 GBrain ----
    success = write_to_gbrain(slug, full_content)
    if not success:
        sys.exit(1)

    # ---- 8. 输出结果 ----
    print(f"✅ 已入库到 {slug}")


if __name__ == "__main__":
    main()
