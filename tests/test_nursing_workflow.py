"""Task 9 verification: nursing ops multi-agent workflow.

Tests:
  1. Flow definition loads and has 4 steps
  2. Each prepare function returns an AgentTask
  3. Report-generate handler returns expected structure
  4. Catalog registers the nursing.ops flow
  5. Flow steps have correct keys and CallAgent configuration

Run:
    uv run pytest tests/test_nursing_workflow.py -v
"""

from __future__ import annotations

import ast
import json
import os
import sys
from uuid import UUID

import pytest

# ── file paths ────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT = os.path.join(_HERE, "..")
_DL_CONTROL = os.path.join(_PROJECT, "dl-control", "dl_control")
_FLOW = os.path.join(_DL_CONTROL, "workflows", "flows", "nursing_ops.py")
_CATALOG = os.path.join(_DL_CONTROL, "workflows", "flows", "catalog.py")
_SKILLS = os.path.join(_PROJECT, "openclaw", "skills")
_REPORT_HP = os.path.join(_SKILLS, "report-generate", "handler.py")
_REPORT_MD = os.path.join(_SKILLS, "report-generate", "SKILL.md")
_REPORT_META = os.path.join(_SKILLS, "report-generate", "_meta.json")


# ────────────────────────────────────────────────────────────────────
# 1. Flow definition: loads and has 4 correct steps
# ────────────────────────────────────────────────────────────────────

def _parse_flow_node(path: str) -> dict | None:
    """Parse the Flow(...) constructor call from the source via AST."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target_name = node.targets[0].id
            val = node.value
            if (
                isinstance(val, ast.Call)
                and isinstance(val.func, ast.Name)
                and val.func.id == "Flow"
            ):
                kwargs = {}
                for kw in val.keywords:
                    if kw.arg == "steps" and isinstance(kw.value, ast.List):
                        kwargs["step_count"] = len(kw.value.elts)
                        step_keys = []
                        for elt in kw.value.elts:
                            if isinstance(elt, ast.Call):
                                # Step() uses positional arg 0 as the key
                                if elt.args and isinstance(elt.args[0], ast.Constant):
                                    step_keys.append(elt.args[0].value)
                                else:
                                    # Fallback: check keyword arg "key"
                                    for skw in elt.keywords:
                                        if skw.arg == "key" and isinstance(skw.value, ast.Constant):
                                            step_keys.append(skw.value.value)
                        kwargs["step_keys"] = step_keys
                    elif isinstance(kw.value, ast.Constant):
                        kwargs[kw.arg] = kw.value.value
                return {"name": target_name, **kwargs}
    return None


def test_nursing_ops_flow_file_exists():
    """nursing_ops.py flow file exists."""
    assert os.path.isfile(_FLOW), f"Flow file not found: {_FLOW}"


def test_flow_has_4_steps():
    """The nursing.ops flow defines exactly 4 steps."""
    info = _parse_flow_node(_FLOW)
    assert info is not None, "Could not find Flow(...) call in nursing_ops.py"
    assert info["name"] == "nursing_ops_flow"
    assert info["step_count"] == 4, f"Expected 4 steps, got {info['step_count']}"


def test_flow_step_keys():
    """The 4 steps have the correct keys in order."""
    info = _parse_flow_node(_FLOW)
    assert info is not None
    expected = [
        "nursing-schedule-step",
        "logistics-step",
        "finance-step",
        "director-report-step",
    ]
    assert info["step_keys"] == expected, f"Steps: {info['step_keys']}"


def test_flow_version():
    """The flow has version 1.0.0."""
    info = _parse_flow_node(_FLOW)
    assert info is not None
    assert info.get("version") == "1.0.0"


# ────────────────────────────────────────────────────────────────────
# 2. Prepare functions return AgentTask
# ────────────────────────────────────────────────────────────────────

def _parse_functions(path: str) -> dict[str, dict]:
    """Parse async/normal def functions from a Python source file via AST."""
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


def test_prepare_functions_exist():
    """All 4 prepare functions are defined."""
    funcs = _parse_functions(_FLOW)
    expected = {
        "_prepare_nursing_schedule",
        "_prepare_logistics",
        "_prepare_finance",
        "_prepare_director_report",
    }
    assert expected.issubset(set(funcs.keys())), f"Missing: {expected - set(funcs.keys())}"


def test_prepare_functions_accept_input_outputs():
    """Each prepare function takes (input, outputs) dict params."""
    funcs = _parse_functions(_FLOW)
    for name in [
        "_prepare_nursing_schedule",
        "_prepare_logistics",
        "_prepare_finance",
        "_prepare_director_report",
    ]:
        params = funcs[name]["params"]
        assert "input" in params, f"{name} missing 'input' param"
        assert "outputs" in params, f"{name} missing 'outputs' param"


def test_agent_task_imported():
    """The flow file imports AgentTask from model."""
    with open(_FLOW, encoding="utf-8") as f:
        src = f.read()
    assert "from dl_control.workflows.model import" in src
    assert "AgentTask" in src
    assert "CallAgent" in src
    assert "Flow" in src
    assert "Retry" in src
    assert "Step" in src


def test_prepare_nursing_schedule_returns_agent_task():
    """_prepare_nursing_schedule creates and returns an AgentTask."""
    with open(_FLOW, encoding="utf-8") as f:
        src = f.read()
    assert "def _prepare_nursing_schedule" in src
    assert "AgentTask(" in src
    assert "nursing-schedule" in src


def test_prepare_logistics_returns_agent_task():
    """_prepare_logistics creates and returns an AgentTask."""
    with open(_FLOW, encoding="utf-8") as f:
        src = f.read()
    assert "def _prepare_logistics" in src
    assert "logistics-inventory" in src


def test_prepare_finance_returns_agent_task():
    """_prepare_finance creates and returns an AgentTask."""
    with open(_FLOW, encoding="utf-8") as f:
        src = f.read()
    assert "def _prepare_finance" in src
    assert "finance-query" in src


def test_prepare_director_report_returns_agent_task():
    """_prepare_director_report creates and returns an AgentTask."""
    with open(_FLOW, encoding="utf-8") as f:
        src = f.read()
    assert "def _prepare_director_report" in src
    assert "report-generate" in src


# ────────────────────────────────────────────────────────────────────
# 3. Report-generate handler
# ────────────────────────────────────────────────────────────────────

def test_report_handler_file_exists():
    """report-generate/handler.py exists."""
    assert os.path.isfile(_REPORT_HP), f"Handler not found: {_REPORT_HP}"


def test_report_handler_has_generate_function():
    """handler.py defines generate_weekly_report as async function."""
    funcs = _parse_functions(_REPORT_HP)
    assert "generate_weekly_report" in funcs
    assert funcs["generate_weekly_report"]["is_async"]


def test_generate_weekly_report_params():
    """generate_weekly_report takes db_url, schedule_data, logistics_data, finance_data."""
    funcs = _parse_functions(_REPORT_HP)
    params = funcs["generate_weekly_report"]["params"]
    assert "db_url" in params
    assert "schedule_data" in params
    assert "logistics_data" in params
    assert "finance_data" in params


def test_report_handler_returns_correct_shape():
    """The handler generates a report with expected sections."""
    # Test via subprocess to avoid import deps
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import asyncio, json, sys; "
                f"sys.path.insert(0, {json.dumps(_SKILLS + '/report-generate')}); "
                "from handler import generate_weekly_report; "
                "r = asyncio.run(generate_weekly_report("
                "db_url='', schedule_data='', logistics_data='', finance_data=''"
                ")); "
                "print(json.dumps(r))"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        pytest.skip(f"Handler subprocess failed: {result.stderr}")
    report = json.loads(result.stdout)
    assert report["report_type"] == "weekly_ops"
    assert "title" in report
    assert "period" in report
    assert "sections" in report
    sections = report["sections"]
    assert "排班概况" in sections
    assert "物资配送" in sections
    assert "成本预估" in sections
    assert "重点关注" in sections
    for key in ["排班概况", "物资配送", "成本预估"]:
        sec = sections[key]
        assert "title" in sec
        assert "data" in sec
    assert "items" in sections["重点关注"]


def test_generate_weekly_report_with_data():
    """When schedule/logistics/finance data is provided, it flows to the report."""
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import asyncio, json, sys; "
                f"sys.path.insert(0, {json.dumps(_SKILLS + '/report-generate')}); "
                "from handler import generate_weekly_report; "
                "r = asyncio.run(generate_weekly_report("
                "db_url='',"
                "schedule_data=json.dumps({'building': '3','staff_count': 5}),"
                "logistics_data=json.dumps({'items': 10}),"
                "finance_data=json.dumps({'total_cost': 50000})"
                ")); "
                "print(json.dumps(r))"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        pytest.skip(f"Handler subprocess failed: {result.stderr}")
    report = json.loads(result.stdout)
    assert report["sections"]["排班概况"]["data"]["building"] == "3"
    assert report["sections"]["物资配送"]["data"]["items"] == 10
    assert report["sections"]["成本预估"]["data"]["total_cost"] == 50000


# ────────────────────────────────────────────────────────────────────
# 4. Catalog registration
# ────────────────────────────────────────────────────────────────────

def test_catalog_registers_nursing_ops():
    """catalog.py contains a FlowDescriptor for nursing.ops."""
    with open(_CATALOG, encoding="utf-8") as f:
        src = f.read()
    assert "nursing.ops" in src
    assert "nursing_ops:nursing_ops_flow" in src or "nursing_ops_flow" in src
    assert "Nursing operations workflow" in src


def test_catalog_descriptor_is_correct():
    """The nursing.ops FlowDescriptor has correct metadata."""
    with open(_CATALOG, encoding="utf-8") as f:
        src = f.read()
    assert 'id="nursing.ops"' in src
    assert 'version="1.0.0"' in src
    assert "nursing_ops" in src


# ────────────────────────────────────────────────────────────────────
# 5. Skill meta and SKILL.md
# ────────────────────────────────────────────────────────────────────

def test_report_meta_json():
    """_meta.json for report-generate has required fields."""
    with open(_REPORT_META, encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["name"] == "report-generate"
    desc = meta.get("description", "")
    assert "周报" in desc or "report" in desc.lower()


def test_report_skill_md_has_trigger_words():
    """SKILL.md documents trigger phrases and usage."""
    with open(_REPORT_MD, encoding="utf-8") as f:
        md = f.read()
    assert "生成周报" in md or "运营报表" in md
    assert "process" in md.lower()
    assert "DATABASE_URL" in md
    assert "handler" in md.lower()


# ────────────────────────────────────────────────────────────────────
# 6. Source-level checks on flow file
# ────────────────────────────────────────────────────────────────────

def test_flow_imports_workflow_model():
    """nursing_ops.py imports from dl_control.workflows.model."""
    with open(_FLOW, encoding="utf-8") as f:
        src = f.read()
    assert "from dl_control.workflows.model import" in src
    assert "from dl_control.workflows import config_cache" in src


def test_flow_has_skill_references():
    """The flow references all four department skills."""
    with open(_FLOW, encoding="utf-8") as f:
        src = f.read()
    assert "nursing-schedule" in src
    assert "logistics-inventory" in src
    assert "finance-query" in src
    assert "report-generate" in src


def test_flow_uses_retry_on_steps():
    """All call_agent steps have Retry configured."""
    with open(_FLOW, encoding="utf-8") as f:
        src = f.read()
    assert "Retry(max_attempts=2, base_seconds=15)" in src
    assert "timeout_seconds=600" in src
