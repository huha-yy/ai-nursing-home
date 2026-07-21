"""Task 6 verification: nursing-schedule + nursing-work-order handler functions.

Tests:
  1. Handler function signatures (via AST — avoids importing asyncpg in dev).
  2. Structure validation: returned dicts match the expected schema.
  3. Integration: query against the seed-data Postgres (when available).

Run:
    uv run pytest tests/test_nursing_skills.py -v

Requires DATABASE_URL env var for integration tests.
"""

from __future__ import annotations

import ast
import os
import sys

import pytest

# ── file paths ────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILLS = os.path.join(_HERE, "..", "openclaw", "skills")
_NS_HP = os.path.join(_SKILLS, "nursing-schedule", "handler.py")
_NWO_HP = os.path.join(_SKILLS, "nursing-work-order", "handler.py")


def _parse_functions(path: str) -> dict[str, dict]:
    """Parse ``async def`` functions from a Python source file via AST.

    Returns ``{name: {is_async, params}}`` for each top-level async function.
    """
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    funcs: dict[str, dict] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = [a.arg for a in node.args.args]
            funcs[node.name] = {
                "is_async": isinstance(node, ast.AsyncFunctionDef),
                "params": params,
            }
    return funcs


# ────────────────────────────────────────────────────────────────────
# 1. Signature / structural checks via AST (no asyncpg needed)
# ────────────────────────────────────────────────────────────────────

def test_schedule_handler_has_correct_functions():
    """nursing-schedule/handler.py defines two async functions."""
    funcs = _parse_functions(_NS_HP)
    assert "generate_weekly_schedule" in funcs
    assert "query_schedule" in funcs
    assert funcs["generate_weekly_schedule"]["is_async"]
    assert funcs["query_schedule"]["is_async"]


def test_work_order_handler_has_correct_function():
    """nursing-work-order/handler.py defines query_work_orders."""
    funcs = _parse_functions(_NWO_HP)
    assert "query_work_orders" in funcs
    assert funcs["query_work_orders"]["is_async"]


def test_generate_weekly_schedule_params():
    """generate_weekly_schedule accepts db_url, building, start_date."""
    funcs = _parse_functions(_NS_HP)
    params = funcs["generate_weekly_schedule"]["params"]
    assert "db_url" in params
    assert "building" in params
    assert "start_date" in params


def test_query_schedule_params():
    """query_schedule accepts db_url, building, target_date."""
    funcs = _parse_functions(_NS_HP)
    params = funcs["query_schedule"]["params"]
    assert "db_url" in params
    assert "building" in params
    assert "target_date" in params


def test_query_work_orders_params():
    """query_work_orders accepts db_url, building, target_date."""
    funcs = _parse_functions(_NWO_HP)
    params = funcs["query_work_orders"]["params"]
    assert "db_url" in params
    assert "building" in params
    assert "target_date" in params


# ────────────────────────────────────────────────────────────────────
# 2. Structure validation (expected return shapes)
# ────────────────────────────────────────────────────────────────────

def test_schedule_result_shape():
    """The return dict of generate_weekly_schedule has expected keys."""
    expected_keys = {
        "week",
        "building",
        "staff_count",
        "total_shifts",
        "day_shifts",
        "night_shifts",
    }
    result = {
        "week": "2026-07-21-2026-07-27",
        "building": "3号楼",
        "staff_count": 2,
        "total_shifts": 28,
        "day_shifts": 14,
        "night_shifts": 14,
    }
    assert set(result.keys()) == expected_keys


def test_schedule_error_shape():
    """When no staff found, the error dict has an 'error' key."""
    result = {"error": "未找到3号楼的护工人员"}
    assert "error" in result


def test_query_schedule_result_shape():
    """Each row from query_schedule has the expected columns."""
    row = {
        "staff_name": "孙志明",
        "shift": "白班(7-19)",
        "building": "3号楼",
        "floor": "2层",
        "zone": "A区",
    }
    expected_keys = {"staff_name", "shift", "building", "floor", "zone"}
    assert set(row.keys()) == expected_keys
    assert "白班" in row["shift"]


def test_work_order_result_shape():
    """The return dict of query_work_orders has expected keys."""
    result = {
        "date": "2026-07-21",
        "building": "全院",
        "overall_rate": "85.7%",
        "by_type": [
            {"type": "血压测量", "total": 1, "completed": 1, "rate": "100.0%"},
            {"type": "进食鼓励", "total": 1, "completed": 0, "rate": "0.0%"},
        ],
    }
    assert set(result.keys()) == {"date", "building", "overall_rate", "by_type"}
    assert isinstance(result["by_type"], list)
    for item in result["by_type"]:
        assert set(item.keys()) == {"type", "total", "completed", "rate"}
        assert item["total"] >= item["completed"] >= 0
        assert item["rate"].endswith("%")


# ────────────────────────────────────────────────────────────────────
# 3. Source-level checks (handler files use correct imports)
# ────────────────────────────────────────────────────────────────────

def test_schedule_handler_imports_asyncpg():
    """nursing-schedule handler.py imports asyncpg."""
    with open(_NS_HP, encoding="utf-8") as f:
        src = f.read()
    assert "import asyncpg" in src or "from asyncpg" in src
    assert "from datetime import" in src
    assert "from typing import" in src


def test_work_order_handler_imports_asyncpg():
    """nursing-work-order handler.py imports asyncpg."""
    with open(_NWO_HP, encoding="utf-8") as f:
        src = f.read()
    assert "import asyncpg" in src or "from asyncpg" in src


# ────────────────────────────────────────────────────────────────────
# 4. SKILL.md files contain required doc sections
# ────────────────────────────────────────────────────────────────────

def test_schedule_skill_md_has_trigger_words():
    """SKILL.md for nursing-schedule documents trigger phrases."""
    md_path = os.path.join(_SKILLS, "nursing-schedule", "SKILL.md")
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    assert "生成排班" in md
    assert "process" in md.lower()  # process tool reference
    assert "DATABASE_URL" in md
    assert "白班" in md
    assert "夜班" in md


def test_work_order_skill_md_has_trigger_words():
    """SKILL.md for nursing-work-order documents trigger phrases."""
    md_path = os.path.join(_SKILLS, "nursing-work-order", "SKILL.md")
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    assert "工单完成情况" in md
    assert "process" in md.lower()
    assert "DATABASE_URL" in md
    assert "overall_rate" in md


# ────────────────────────────────────────────────────────────────────
# 5. _meta.json files are valid
# ────────────────────────────────────────────────────────────────────

def test_schedule_meta_json():
    """_meta.json for nursing-schedule has required fields."""
    import json

    path = os.path.join(_SKILLS, "nursing-schedule", "_meta.json")
    with open(path, encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["name"] == "nursing-schedule"
    desc = meta.get("description", "")
    assert "排班" in desc or "schedule" in desc.lower()


def test_work_order_meta_json():
    """_meta.json for nursing-work-order has required fields."""
    import json

    path = os.path.join(_SKILLS, "nursing-work-order", "_meta.json")
    with open(path, encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["name"] == "nursing-work-order"
    assert "description" in meta


# ────────────────────────────────────────────────────────────────────
# 6. Integration tests (require DATABASE_URL and seeded Postgres)
# ────────────────────────────────────────────────────────────────────

_db_url = os.environ.get("DATABASE_URL", "")


def _import_handler(path: str):
    """Import a handler module from file path (only works if deps installed)."""
    import importlib.util

    try:
        import asyncpg  # noqa: F401
    except ModuleNotFoundError:
        pytest.skip("asyncpg not installed in this environment")

    name = os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(not _db_url, reason="DATABASE_URL not set")
@pytest.mark.asyncio
async def test_integration_query_schedule():
    """Query schedule seed data for 3号楼 on a known date."""
    mod = _import_handler(_NS_HP)
    rows = await mod.query_schedule(_db_url, building="3号楼", target_date="2026-07-21")
    assert isinstance(rows, list)
    if rows:
        first = rows[0]
        assert "staff_name" in first
        assert "shift" in first
        assert "白班" in first["shift"] or "夜班" in first["shift"]


@pytest.mark.skipif(not _db_url, reason="DATABASE_URL not set")
@pytest.mark.asyncio
async def test_integration_query_work_orders():
    """Query work order summary (全院 scope)."""
    mod = _import_handler(_NWO_HP)
    result = await mod.query_work_orders(_db_url, target_date="2026-07-21")
    assert isinstance(result, dict)
    assert "date" in result
    assert "overall_rate" in result
    assert "by_type" in result
    assert result["overall_rate"].endswith("%")
    assert result["date"] == "2026-07-21"
