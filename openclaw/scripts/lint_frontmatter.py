#!/usr/bin/env python3
"""
GBrain 知识库 Frontmatter 校验器。

校验 .md 文件的 YAML frontmatter 是否符合 schema_rules.yaml 定义。
支持单文件/目录模式、自动修复、CI 友好退出码。

用法:
  # 检查单个文件
  python3 lint_frontmatter.py --path daien/product/care_robot.md

  # 递归检查整个目录
  python3 lint_frontmatter.py --path brain-repo/daien/

  # 检查并自动修复（补全缺少的可选字段）
  python3 lint_frontmatter.py --path brain-repo/ --fix

  # 指定 schema 文件
  python3 lint_frontmatter.py --path brain-repo/ --schema /path/to/schema_rules.yaml

退出码:
  0 — 全部通过
  1 — 有文件不合格（缺少必填字段）
  2 — 有警告（缺少可选字段但必填齐全）

输出格式:
  [PASS]   文件路径       — 全部字段合规
  [FIXED]  文件路径       — 补充了可选字段 (+tags, +created)
  [WARN]   文件路径       — 缺少可选字段: tags（--fix 可自动修复）
  [FAIL]   文件路径       — 缺少必填字段: tags（拒绝入库）
  [SKIP]   文件路径       — 无法解析 frontmatter（跳过）
"""

import argparse
import os
import re
import sys
import yaml

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

SchemaType = dict  # {"required_frontmatter": [...], "optional_frontmatter": [...]}


def load_schema(path: str | None = None) -> dict[str, SchemaType]:
    """加载 schema_rules.yaml，返回 {type_name: rules} 映射。"""
    if path is None:
        path = os.path.join(SCRIPTS_DIR, "schema_rules.yaml")
    if not os.path.isfile(path):
        print(f"[错误: schema 文件不存在: {path}]", file=sys.stderr)
        sys.exit(2)
    with open(path, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    return doc.get("types", {})


def parse_frontmatter(content: str) -> tuple[dict | None, str | None]:
    """解析 markdown frontmatter。

    返回: (frontmatter_dict, body_after_frontmatter)
          如果 frontmatter 解析失败返回 (None, None)
    """
    # 匹配 YAML frontmatter: 以 --- 开头，后跟 YAML，以 --- 结尾
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", content, re.DOTALL)
    if not match:
        # 尝试没有内容的 case：---\n...\n--- 后无内容
        match = re.match(r"^---\s*\n(.*?)\n---\s*$", content, re.DOTALL)
        if not match:
            return None, None
    raw_yaml = match.group(1)
    body = match.group(2) if len(match.groups()) > 1 else ""
    try:
        fm = yaml.safe_load(raw_yaml)
        if not isinstance(fm, dict):
            return None, None
        return fm, body
    except yaml.YAMLError:
        return None, None


def validate_frontmatter_dict(
    fm: dict, content_type: str, schema: dict[str, SchemaType]
) -> tuple[list[str], list[str], list[str]]:
    """校验 frontmatter 字典对象（不涉及文件 I/O）。

    参数:
        fm — frontmatter 字典
        content_type — type 字段值（如 "product", "faq"）
        schema — load_schema() 返回的完整 schema

    返回:
        (missing_required, missing_optional, unknown_fields)
    """
    rules = schema.get(content_type)
    if rules is None:
        # 未知类型：无法校验，视为 empty 列表
        return [], [], []

    required = set(rules.get("required_frontmatter", []))
    optional = set(rules.get("optional_frontmatter", []))

    existing = {k.lower(): k for k in fm.keys()}

    all_known = required | optional
    unknown_fields = [
        k for k in fm.keys()
        if k.lower() not in {x.lower() for x in all_known}
    ]

    missing_required = []
    for field in required:
        if field.lower() not in existing:
            missing_required.append(field)

    missing_optional = []
    for field in optional:
        if field.lower() not in existing:
            missing_optional.append(field)

    return missing_required, missing_optional, unknown_fields


def check_file(
    file_path: str, schema: dict[str, SchemaType], fix: bool = False
) -> int:
    """检查单个文件。

    返回: 0=通过, 1=必填缺（FAIL）, 2=可选缺（WARN）
    """
    rel_path = os.path.relpath(file_path)
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    fm, body = parse_frontmatter(content)
    if fm is None:
        print(f"  [SKIP]  {rel_path} — 无法解析 frontmatter")
        return 0

    content_type = fm.get("type", "")
    if not content_type:
        print(f"  [SKIP]  {rel_path} — type 字段缺失，无法匹配 schema")
        return 0

    rules = schema.get(content_type)
    if rules is None:
        print(f"  [SKIP]  {rel_path} — 未知类型 '{content_type}'，无法校验")
        return 0

    missing_required, missing_optional, _unknown = validate_frontmatter_dict(
        fm, content_type, schema
    )

    # 处理 --fix：补充缺少的可选字段
    if fix and missing_optional:
        for field in sorted(missing_optional):
            if field == "created":
                from datetime import date
                fm[field] = date.today().strftime("%Y-%m-%d")
            elif field == "tags":
                fm[field] = ""
            elif field in ("version", "duration", "products", "pricing", "constitution"):
                fm[field] = ""
            else:
                fm[field] = ""

        # 重建 frontmatter
        new_fm_lines = "---\n"
        for key, value in fm.items():
            if isinstance(value, str):
                new_fm_lines += f"{key}: {value}\n"
            elif value is None:
                new_fm_lines += f"{key}:\n"
            else:
                new_fm_lines += f"{key}: {value}\n"
        new_fm_lines += "---"

        if body:
            new_content = new_fm_lines + "\n" + body
        else:
            new_content = new_fm_lines + "\n"

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        added = ", ".join(f"+{f}" for f in sorted(missing_optional))
        print(f"  [FIXED] {rel_path} — {added}")
        missing_optional = []  # 已修复

    # 输出结果
    if missing_required:
        fields = ", ".join(missing_required)
        print(f"  [FAIL]  {rel_path} — 缺少必填字段: {fields}")
        return 1
    elif missing_optional:
        fields = ", ".join(missing_optional)
        print(f"  [WARN]  {rel_path} — 缺少可选字段: {fields}")
        return 2
    else:
        print(f"  [PASS]  {rel_path}")
        return 0


def main():
    parser = argparse.ArgumentParser(
        description="GBrain 知识库 Frontmatter 校验器"
    )
    parser.add_argument(
        "--path",
        required=True,
        help="文件或目录路径",
    )
    parser.add_argument(
        "--schema",
        help="schema_rules.yaml 路径（默认 scripts 目录下的）",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="自动修复缺少的可选字段",
    )
    args = parser.parse_args()

    schema = load_schema(args.schema)
    target = args.path

    if not os.path.exists(target):
        print(f"[错误: 路径不存在: {target}]", file=sys.stderr)
        sys.exit(2)

    if os.path.isfile(target):
        files = [target]
    else:
        files = []
        for root, _, filenames in os.walk(target):
            for fn in sorted(filenames):
                if fn.endswith(".md"):
                    files.append(os.path.join(root, fn))

    if not files:
        print(f"[警告: 未找到 .md 文件]", file=sys.stderr)
        sys.exit(0)

    count_fail = 0
    count_warn = 0
    count_pass = 0
    count_skip = 0

    for file_path in files:
        result = check_file(file_path, schema, fix=args.fix)
        if result == 1:
            count_fail += 1
        elif result == 2:
            count_warn += 1
        elif result == 0:
            # 需要区分 PASS 和 SKIP
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            fm, _ = parse_frontmatter(content)
            if fm is None or not fm.get("type"):
                count_skip += 1
            else:
                count_pass += 1

    # 统计
    total = len(files)
    print()
    print(f"总计: {total} 文件 | ✅ {count_pass} 通过 | ⚠️ {count_warn} 警告 | ❌ {count_fail} 不合格 | ⏭️ {count_skip} 跳过")

    if count_fail > 0:
        sys.exit(1)
    if count_warn > 0:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
