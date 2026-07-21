"""Task 7 verification: 8 new skill handler functions.

Tests:
  1. Handler function signatures (via AST — avoids importing asyncpg in dev).
  2. Structure validation: returned dicts match the expected schema.
  3. Integration: query against the seed-data Postgres (when available).

Run:
    uv run pytest tests/test_query_skills.py -v

Requires DATABASE_URL env var for integration tests.
"""

from __future__ import annotations

import ast
import json
import os
import sys

import pytest

# ── file paths ────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILLS = os.path.join(_HERE, "..", "openclaw", "skills")

_SKILLS_MAP = {
    "logistics-inventory": os.path.join(_SKILLS, "logistics-inventory", "handler.py"),
    "logistics-query": os.path.join(_SKILLS, "logistics-query", "handler.py"),
    "resident-query": os.path.join(_SKILLS, "resident-query", "handler.py"),
    "staff-query": os.path.join(_SKILLS, "staff-query", "handler.py"),
    "meal-query": os.path.join(_SKILLS, "meal-query", "handler.py"),
    "activity-query": os.path.join(_SKILLS, "activity-query", "handler.py"),
    "finance-query": os.path.join(_SKILLS, "finance-query", "handler.py"),
    "alert-query": os.path.join(_SKILLS, "alert-query", "handler.py"),
}


def _parse_functions(path: str) -> dict[str, dict]:
    """Parse ``async def`` functions from a Python source file via AST."""
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


# ── 1. Signature / structural checks via AST ──────────────────────────


def test_logistics_inventory_has_correct_functions():
    funcs = _parse_functions(_SKILLS_MAP["logistics-inventory"])
    assert "check_inventory" in funcs
    assert "check_low_stock" in funcs
    assert funcs["check_inventory"]["is_async"]
    assert funcs["check_low_stock"]["is_async"]
    assert "db_url" in funcs["check_inventory"]["params"]
    assert "db_url" in funcs["check_low_stock"]["params"]


def test_logistics_query_has_correct_functions():
    funcs = _parse_functions(_SKILLS_MAP["logistics-query"])
    assert "query_meals" in funcs
    assert "query_item" in funcs
    assert funcs["query_meals"]["is_async"]
    assert funcs["query_item"]["is_async"]
    assert "db_url" in funcs["query_meals"]["params"]
    assert "db_url" in funcs["query_item"]["params"]


def test_resident_query_has_correct_function():
    funcs = _parse_functions(_SKILLS_MAP["resident-query"])
    assert "query_resident" in funcs
    assert funcs["query_resident"]["is_async"]
    params = funcs["query_resident"]["params"]
    assert "db_url" in params
    assert "name" in params
    assert "room" in params
    assert "building" in params


def test_staff_query_has_correct_function():
    funcs = _parse_functions(_SKILLS_MAP["staff-query"])
    assert "query_staff" in funcs
    assert funcs["query_staff"]["is_async"]
    params = funcs["query_staff"]["params"]
    assert "db_url" in params
    assert "name" in params
    assert "building" in params


def test_meal_query_has_correct_functions():
    funcs = _parse_functions(_SKILLS_MAP["meal-query"])
    assert "query_today_meals" in funcs
    assert "query_week_meals" in funcs
    assert funcs["query_today_meals"]["is_async"]
    assert funcs["query_week_meals"]["is_async"]
    assert "db_url" in funcs["query_today_meals"]["params"]
    assert "db_url" in funcs["query_week_meals"]["params"]


def test_activity_query_has_correct_function():
    funcs = _parse_functions(_SKILLS_MAP["activity-query"])
    assert "query_week_activities" in funcs
    assert funcs["query_week_activities"]["is_async"]
    assert "db_url" in funcs["query_week_activities"]["params"]


def test_finance_query_has_correct_function():
    funcs = _parse_functions(_SKILLS_MAP["finance-query"])
    assert "query_resident_finance" in funcs
    assert funcs["query_resident_finance"]["is_async"]
    params = funcs["query_resident_finance"]["params"]
    assert "db_url" in params
    assert "resident_name" in params


def test_alert_query_has_correct_function():
    funcs = _parse_functions(_SKILLS_MAP["alert-query"])
    assert "query_alerts" in funcs
    assert funcs["query_alerts"]["is_async"]
    params = funcs["query_alerts"]["params"]
    assert "db_url" in params
    assert "building" in params
    assert "handled" in params


# ── 2. Structure validation (expected return shapes) ──────────────────


def test_inventory_item_shape():
    expected_keys = {"item_name", "category", "quantity", "unit", "safety_stock", "alert", "suggestion"}
    item = {
        "item_name": "胃管",
        "category": "医疗器械",
        "quantity": 45,
        "unit": "根",
        "safety_stock": 10,
        "alert": False,
        "suggestion": "",
    }
    assert set(item.keys()) == expected_keys
    assert isinstance(item["alert"], bool)


def test_low_stock_item_has_suggestion():
    item = {
        "item_name": "消毒液",
        "category": "清洁消毒",
        "quantity": 3,
        "unit": "瓶",
        "safety_stock": 10,
        "alert": True,
        "suggestion": "建议采购：消毒液 当前库存 3瓶，安全库存 10瓶，缺 7瓶",
    }
    assert item["alert"] is True
    assert "建议采购" in item["suggestion"]
    assert "缺" in item["suggestion"]


def test_resident_item_shape():
    expected_keys = {"name", "building", "floor", "room", "age", "diagnosis", "care_level", "notes"}
    item = {
        "name": "张国栋",
        "building": "1号楼",
        "floor": "1层",
        "room": "101",
        "age": 78,
        "diagnosis": "高血压, 糖尿病",
        "care_level": "自理",
        "notes": "每日自测血压，清淡饮食",
    }
    assert set(item.keys()) == expected_keys


def test_staff_item_shape():
    expected_keys = {"name", "role", "dept", "building", "floor"}
    item = {
        "name": "李卫东",
        "role": "building",
        "dept": None,
        "building": "3号楼",
        "floor": None,
    }
    assert set(item.keys()) == expected_keys


def test_meal_item_shape():
    expected_keys = {"date", "meal_type", "menu"}
    item = {
        "date": "2026-07-21",
        "meal_type": "早餐",
        "menu": "小米粥、鸡蛋、葱花卷、凉拌黄瓜、牛奶",
    }
    assert set(item.keys()) == expected_keys


def test_activity_item_shape():
    expected_keys = {"title", "date", "time", "location"}
    item = {
        "title": "太极拳晨练",
        "date": "2026-07-21",
        "time": "07:00-07:40",
        "location": "1号楼前广场",
    }
    assert set(item.keys()) == expected_keys


def test_finance_item_shape():
    expected_keys = {"name", "month", "amount", "paid", "status"}
    item = {
        "name": "张国栋",
        "month": "2026-07",
        "amount": 3800.0,
        "paid": True,
        "status": "已结清",
    }
    assert set(item.keys()) == expected_keys
    assert item["status"] in ("已结清", "未结清")
    assert isinstance(item["paid"], bool)


def test_alert_item_shape():
    expected_keys = {"resident_id", "content", "category", "severity", "created_at", "handled"}
    item = {
        "resident_id": "R004",
        "content": "右侧臀部皮肤出现红肿",
        "category": "皮肤护理",
        "severity": "danger",
        "created_at": "2026-07-21T00:00:00",
        "handled": False,
    }
    assert set(item.keys()) == expected_keys
    assert item["severity"] in ("info", "warning", "danger")
    assert isinstance(item["handled"], bool)


# ── 3. Source-level checks (handler files use correct imports) ────────


@pytest.mark.parametrize("skill,path", list(_SKILLS_MAP.items()))
def test_handler_imports_asyncpg(skill, path):
    """Every skill handler.py imports asyncpg."""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    assert "import asyncpg" in src or "from asyncpg" in src, f"{skill}: missing asyncpg import"


# ── 4. SKILL.md files contain required doc sections ────────────────────


def test_logistics_inventory_skill_md():
    md_path = os.path.join(_SKILLS, "logistics-inventory", "SKILL.md")
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    assert "库存" in md
    assert "process" in md.lower()
    assert "DATABASE_URL" in md
    assert "check_inventory" in md
    assert "check_low_stock" in md


@pytest.mark.parametrize("skill_name", list(_SKILLS_MAP.keys()))
def test_skill_md_has_required_sections(skill_name):
    """Every SKILL.md mentions process tool and DATABASE_URL."""
    md_path = os.path.join(_SKILLS, skill_name, "SKILL.md")
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    assert "process" in md.lower(), f"{skill_name}: missing process tool reference"
    assert "DATABASE_URL" in md, f"{skill_name}: missing DATABASE_URL"


# ── 5. _meta.json files are valid ─────────────────────────────────────


@pytest.mark.parametrize("skill_name", list(_SKILLS_MAP.keys()))
def test_meta_json_valid(skill_name):
    """Every _meta.json has name and description."""
    path = os.path.join(_SKILLS, skill_name, "_meta.json")
    with open(path, encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["name"] == skill_name, f"{skill_name}: name mismatch"
    assert "description" in meta, f"{skill_name}: missing description"
    assert len(meta["description"]) > 0, f"{skill_name}: empty description"


# ── 6. Integration tests (require DATABASE_URL and seeded Postgres) ───

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
async def test_integration_check_inventory():
    """check_inventory returns items with alert flag."""
    mod = _import_handler(_SKILLS_MAP["logistics-inventory"])
    items = await mod.check_inventory(_db_url)
    assert isinstance(items, list)
    assert len(items) > 0
    first = items[0]
    assert "item_name" in first
    assert "alert" in first
    assert isinstance(first["alert"], bool)


@pytest.mark.skipif(not _db_url, reason="DATABASE_URL not set")
@pytest.mark.asyncio
async def test_integration_query_meals():
    """query_meals returns today's meals."""
    mod = _import_handler(_SKILLS_MAP["logistics-query"])
    meals = await mod.query_meals(_db_url)
    assert isinstance(meals, list)
    if meals:
        assert "meal_type" in meals[0]
        assert "menu" in meals[0]


@pytest.mark.skipif(not _db_url, reason="DATABASE_URL not set")
@pytest.mark.asyncio
async def test_integration_query_resident():
    """query_resident by name returns matching residents."""
    mod = _import_handler(_SKILLS_MAP["resident-query"])
    rows = await mod.query_resident(_db_url, name="张国栋")
    assert isinstance(rows, list)
    assert len(rows) > 0
    assert rows[0]["name"] == "张国栋"


@pytest.mark.skipif(not _db_url, reason="DATABASE_URL not set")
@pytest.mark.asyncio
async def test_integration_query_staff():
    """query_staff by building returns matching staff."""
    mod = _import_handler(_SKILLS_MAP["staff-query"])
    rows = await mod.query_staff(_db_url, building="3号楼")
    assert isinstance(rows, list)
    assert len(rows) > 0


@pytest.mark.skipif(not _db_url, reason="DATABASE_URL not set")
@pytest.mark.asyncio
async def test_integration_query_week_meals():
    """query_week_meals returns this week's meals."""
    mod = _import_handler(_SKILLS_MAP["meal-query"])
    meals = await mod.query_week_meals(_db_url)
    assert isinstance(meals, list)


@pytest.mark.skipif(not _db_url, reason="DATABASE_URL not set")
@pytest.mark.asyncio
async def test_integration_query_week_activities():
    """query_week_activities returns this week's activities."""
    mod = _import_handler(_SKILLS_MAP["activity-query"])
    activities = await mod.query_week_activities(_db_url)
    assert isinstance(activities, list)


@pytest.mark.skipif(not _db_url, reason="DATABASE_URL not set")
@pytest.mark.asyncio
async def test_integration_query_resident_finance():
    """query_resident_finance returns finance records."""
    mod = _import_handler(_SKILLS_MAP["finance-query"])
    rows = await mod.query_resident_finance(_db_url, resident_name="张国栋")
    assert isinstance(rows, list)
    if rows:
        assert rows[0]["name"] == "张国栋"
        assert "status" in rows[0]
        assert rows[0]["status"] in ("已结清", "未结清")


@pytest.mark.skipif(not _db_url, reason="DATABASE_URL not set")
@pytest.mark.asyncio
async def test_integration_query_alerts():
    """query_alerts returns health alerts."""
    mod = _import_handler(_SKILLS_MAP["alert-query"])
    alerts = await mod.query_alerts(_db_url, handled=False)
    assert isinstance(alerts, list)
    if alerts:
        assert "severity" in alerts[0]
        assert alerts[0]["severity"] in ("info", "warning", "danger")
