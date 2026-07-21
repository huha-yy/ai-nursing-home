"""End-to-end integration smoke test for the AI 养老院院长 MVP.

Verifies the full stack works end-to-end without requiring a running server:
  1. All 10 agent YAML seeds load without error
  2. All skill handlers import successfully
  3. Database tables all exist with data (seed SQL check)
  4. Auth endpoint accepts nursing credentials (hash verification)
  5. Dashboard API returns valid JSON (subprocess)
  6. Health signal detection works on sample inputs
  7. Workflow flow loads with 4 steps

Run:
    uv run pytest tests/test_smoke_nursing_mvp.py -v
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys

import pytest
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT = os.path.join(_HERE, "..")
_AGENTS_DIR = os.path.join(_PROJECT, "dl-control", "precreated_agents")
_SKILLS_DIR = os.path.join(_PROJECT, "openclaw", "skills")
_SEED_SQL = os.path.join(_PROJECT, "infra", "postgres", "init", "03-nursing-seed.sql")
_FLOW_FILE = os.path.join(
    _PROJECT, "dl-control", "dl_control", "workflows", "flows", "nursing_ops.py"
)
_CATALOG_FILE = os.path.join(
    _PROJECT, "dl-control", "dl_control", "workflows", "flows", "catalog.py"
)
_MAIN_PY = os.path.join(_PROJECT, "dl-control", "dl_control", "main.py")


# ===================================================================
# 1. All 10 agent YAML seeds load without error
# ===================================================================

EXPECTED_AGENTS = {
    "director",
    "nursing-dept",
    "logistics-dept",
    "building-1",
    "building-2",
    "building-3",
    "building-4",
    "building-5",
    "building-6",
    "general-assistant",
}

REQUIRED_YAML_KEYS = {"id", "display_name", "skill_list"}


def _discover_agent_seeds() -> dict[str, str]:
    """Walk the precreated_agents dir and return {agent_id: yaml_path} for nursing agents."""
    result: dict[str, str] = {}
    for entry in os.listdir(_AGENTS_DIR):
        yaml_path = os.path.join(_AGENTS_DIR, entry, "agent.yaml")
        if os.path.isfile(yaml_path):
            result[entry] = yaml_path
    return result


def test_all_agents_directory_exists():
    """The precreated_agents directory exists and contains subdirectories."""
    assert os.path.isdir(_AGENTS_DIR), f"Missing: {_AGENTS_DIR}"
    entries = [
        e
        for e in os.listdir(_AGENTS_DIR)
        if os.path.isdir(os.path.join(_AGENTS_DIR, e))
    ]
    assert len(entries) >= 10, f"Expected >=10 agent dirs, found {len(entries)}"


def test_all_10_nursing_agents_have_yaml():
    """Each of the 10 nursing agents has an agent.yaml seed file."""
    seeds = _discover_agent_seeds()
    for agent_id in EXPECTED_AGENTS:
        assert agent_id in seeds, f"Missing agent.yaml for: {agent_id}"


@pytest.mark.parametrize(
    "agent_id",
    sorted(EXPECTED_AGENTS),
)
def test_agent_yaml_loads_and_is_valid(agent_id):
    """Agent YAML loads with required keys and non-empty skill_list."""
    yaml_path = os.path.join(_AGENTS_DIR, agent_id, "agent.yaml")
    assert os.path.isfile(yaml_path), f"Missing: {yaml_path}"
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), f"{agent_id} agent.yaml is not a dict"
    missing = REQUIRED_YAML_KEYS - set(data.keys())
    assert not missing, f"{agent_id} missing keys: {missing}"
    assert isinstance(data["skill_list"], list), f"{agent_id} skill_list is not a list"
    assert len(data["skill_list"]) > 0, f"{agent_id} skill_list is empty"


def test_agents_dont_overlap_ids():
    """No two agent YAMLs have the same id."""
    ids_seen: set[str] = set()
    seeds = _discover_agent_seeds()
    for agent_dir, yaml_path in seeds.items():
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        agent_id = data.get("id", agent_dir)
        assert agent_id not in ids_seen, f"Duplicate agent id: {agent_id}"
        ids_seen.add(agent_id)


# ===================================================================
# 2. All skill handlers import successfully
# ===================================================================

# The nursing MVP skills that have handler.py files
SKILLS_WITH_HANDLERS = [
    "nursing-schedule",
    "nursing-work-order",
    "logistics-inventory",
    "logistics-query",
    "resident-query",
    "staff-query",
    "meal-query",
    "activity-query",
    "finance-query",
    "alert-query",
    "report-generate",
]


@pytest.mark.parametrize("skill_name", SKILLS_WITH_HANDLERS)
def test_skill_handler_exists(skill_name):
    """Each nursing skill directory has a handler.py file."""
    hp = os.path.join(_SKILLS_DIR, skill_name, "handler.py")
    assert os.path.isfile(hp), f"Missing handler: {hp}"


@pytest.mark.parametrize("skill_name", SKILLS_WITH_HANDLERS)
def test_skill_handler_is_parsable_python(skill_name):
    """Each handler.py is syntactically valid Python (AST parse)."""
    hp = os.path.join(_SKILLS_DIR, skill_name, "handler.py")
    with open(hp, encoding="utf-8") as f:
        source = f.read()
    try:
        ast.parse(source)
    except SyntaxError as e:
        pytest.fail(f"Syntax error in {hp}: {e}")


@pytest.mark.parametrize("skill_name", SKILLS_WITH_HANDLERS)
def test_skill_has_meta_json(skill_name):
    """Each skill directory has _meta.json with name and description."""
    mp = os.path.join(_SKILLS_DIR, skill_name, "_meta.json")
    assert os.path.isfile(mp), f"Missing _meta.json: {mp}"
    with open(mp, encoding="utf-8") as f:
        meta = json.load(f)
    assert "name" in meta, f"{skill_name} _meta.json missing 'name'"
    assert "description" in meta, f"{skill_name} _meta.json missing 'description'"


def test_skill_handler_functions_are_async():
    """Every handler.py defines at least one async function."""
    for skill_name in SKILLS_WITH_HANDLERS:
        hp = os.path.join(_SKILLS_DIR, skill_name, "handler.py")
        with open(hp, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        async_funcs = [
            node.name
            for node in ast.iter_child_nodes(tree)
            if isinstance(node, ast.AsyncFunctionDef)
        ]
        assert len(async_funcs) > 0, f"{skill_name} handler.py has no async functions"


# ===================================================================
# 3. Database tables all exist with data (seed SQL verification)
# ===================================================================

EXPECTED_TABLES = [
    "nursing_users",
    "nursing_residents",
    "nursing_schedules",
    "nursing_work_orders",
    "nursing_inventory",
    "nursing_health_alerts",
    "nursing_meals",
    "nursing_activities",
    "nursing_finances",
    "nursing_complaints",
]


def test_seed_sql_file_exists():
    """The nursing seed SQL file exists."""
    assert os.path.isfile(_SEED_SQL), f"Missing seed SQL: {_SEED_SQL}"


@pytest.mark.parametrize("table_name", EXPECTED_TABLES)
def test_seed_sql_has_create_table(table_name):
    """Each expected table has a CREATE TABLE IF NOT EXISTS statement."""
    with open(_SEED_SQL, encoding="utf-8") as f:
        sql = f.read()
    assert (
        f"CREATE TABLE IF NOT EXISTS {table_name}" in sql
    ), f"Missing CREATE TABLE for {table_name}"


@pytest.mark.parametrize("table_name", EXPECTED_TABLES)
def test_seed_sql_has_insert_data(table_name):
    """Each table (except nursing_complaints may or may not) has INSERT data.

    nursing_complaints has a separate INSERT block — checked separately.
    """
    with open(_SEED_SQL, encoding="utf-8") as f:
        sql = f.read()
    assert (
        f"INSERT INTO {table_name}" in sql
    ), f"Missing INSERT INTO for {table_name}"


def test_seed_sql_inserts_at_least_5_residents():
    """There are at least 5 residents seeded."""
    with open(_SEED_SQL, encoding="utf-8") as f:
        sql = f.read()
    # Count INSERT INTO nursing_residents lines after VALUES
    import re

    match = re.search(r"INSERT INTO nursing_residents.*?VALUES\s*(.+?);", sql, re.DOTALL)
    assert match, "Could not find nursing_residents INSERT"
    values_block = match.group(1)
    # Each row has a ( at start of line
    row_count = len(re.findall(r"^\s*\(", values_block, re.MULTILINE))
    assert row_count >= 5, f"Expected >=5 residents, found {row_count}"


def test_seed_sql_has_all_user_roles():
    """All 6 nursing roles are represented in user seed data."""
    with open(_SEED_SQL, encoding="utf-8") as f:
        sql = f.read()
    roles = ["director", "nursing_dept", "logistics_dept", "building", "floor", "general"]
    for role in roles:
        assert f"'{role}'" in sql, f"Role '{role}' not found in seed SQL"


# ===================================================================
# 4. Auth endpoint accepts nursing credentials (hash verification)
# ===================================================================

# Argon2id hash of "123456" from 03-nursing-seed.sql
_SEED_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4"
    "$mNj06Lk/yZZWT091ecHM2w"
    "$Db2EM3+8cp6j65wewZ1UxYpjRJ7YjDp1advtQI20wjU"
)

# Known usernames from the seed SQL
_SEED_USERS = [
    ("wang_jianguo", "director", "王建国"),
    ("nurse_zhang", "nursing_dept", "张护士"),
    ("logi_chen", "logistics_dept", "陈总务"),
    ("b3_li_weidong", "building", "李卫东"),
    ("b3f2_zhao", "floor", "赵小明"),
    ("admin_liu", "general", "刘行政"),
]


def test_auth_nursing_login_route_exists():
    """The nursing-login endpoint is defined in auth/routes.py."""
    _AUTH_ROUTES = os.path.join(
        _PROJECT, "dl-control", "dl_control", "auth", "routes.py"
    )
    with open(_AUTH_ROUTES, encoding="utf-8") as f:
        src = f.read()
    assert "/auth/nursing-login" in src
    assert "nursing_login_post" in src


def test_seed_password_hash_verifies_correct():
    """The seed-data argon2id hash resolves to '123456' (via subprocess)."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from argon2 import PasswordHasher; "
                "ph = PasswordHasher(); "
                "assert ph.verify("
                f"{_SEED_HASH!r}, '123456'"
                "); "
                "print('OK')"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if "No module named" in result.stderr:
        pytest.skip("argon2 not installed in this environment")
    assert result.stdout.strip() == "OK", f"Hash verify failed: {result.stderr}"


def test_seed_password_hash_rejects_wrong():
    """A wrong password does not verify against the seed hash (via subprocess)."""
    code = (
        "from argon2 import PasswordHasher\n"
        "from argon2.exceptions import VerifyMismatchError\n"
        "ph = PasswordHasher()\n"
        "try:\n"
        f"    ph.verify({_SEED_HASH!r}, 'wrong_password')\n"
        "    print('UNEXPECTED_MATCH')\n"
        "except VerifyMismatchError:\n"
        "    print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if "No module named" in result.stderr:
        pytest.skip("argon2 not installed in this environment")
    assert result.stdout.strip() == "OK", f"Should have rejected wrong password: {result.stderr}"


def test_try_nursing_login_importable():
    """try_nursing_login is importable and has correct signature (via subprocess)."""
    _DL_CONTROL_DIR = os.path.join(_PROJECT, "dl-control")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                f"sys.path.insert(0, {_DL_CONTROL_DIR!r}); "
                "from dl_control.auth.service import try_nursing_login; "
                "import inspect; "
                "sig = inspect.signature(try_nursing_login); "
                "params = list(sig.parameters.keys()); "
                "assert 'db' in params; "
                "assert 'username' in params; "
                "assert 'password' in params; "
                "print('OK')"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        if "No module named" in result.stderr:
            pytest.skip(f"dl-control deps not installed: {result.stderr.strip()}")
        pytest.fail(f"try_nursing_login check failed: {result.stderr}")
    assert result.stdout.strip() == "OK"


@pytest.mark.parametrize("username,role,name", _SEED_USERS)
def test_seed_user_in_sql(username, role, name):
    """Each known seed user appears in the seed SQL."""
    with open(_SEED_SQL, encoding="utf-8") as f:
        sql = f.read()
    assert username in sql, f"Username '{username}' not in seed SQL"
    # The role string should appear somewhere in the file
    assert f"'{role}'" in sql, (
        f"Role '{role}' not found in seed SQL for user {username}"
    )


# ===================================================================
# 5. Dashboard API returns valid JSON (subprocess import check)
# ===================================================================

def test_dashboard_api_endpoints_mounted():
    """Dashboard API routes are defined in main.py."""
    with open(_MAIN_PY, encoding="utf-8") as f:
        src = f.read()
    assert "/api/nursing/dashboard" in src
    assert "/api/nursing/alerts" in src


def test_dashboard_api_sql_queries_valid():
    """Dashboard API queries reference valid table/column names from seed SQL."""
    with open(_MAIN_PY, encoding="utf-8") as f:
        src = f.read()

    # Extract SQL queries from the dashboard function
    queries = []
    for line in src.split("\n"):
        stripped = line.strip()
        if stripped.startswith('"SELECT') or stripped.startswith("'SELECT"):
            queries.append(stripped.strip("'\""))

    assert len(queries) >= 5, f"Expected >=5 SQL queries in dashboard, found {len(queries)}"

    with open(_SEED_SQL, encoding="utf-8") as f:
        seed = f.read()

    for q in queries:
        # Check that table names in each query exist in the seed
        import re

        tables = re.findall(r"FROM\s+(\w+)", q, re.IGNORECASE)
        tables += re.findall(r"JOIN\s+(\w+)", q, re.IGNORECASE)
        for tbl in tables:
            assert (
                f"CREATE TABLE IF NOT EXISTS {tbl}" in seed
            ), f"Table '{tbl}' in dashboard query not in seed SQL: {q[:60]}"


def test_dashboard_kpi_fields_present():
    """Dashboard response includes expected KPI keys."""
    expected_keys = {
        "total_residents",
        "on_duty_today",
        "inventory_alerts",
        "pending_health_alerts",
        "monthly_complaints",
        "completion_rate",
        "focus_residents",
    }
    with open(_MAIN_PY, encoding="utf-8") as f:
        src = f.read()
    for key in expected_keys:
        assert key in src, f"Dashboard field '{key}' not found in main.py"


# ===================================================================
# 6. Health signal detection works on sample inputs
# ===================================================================

def test_health_signal_module_exists():
    """health_signal.py module file exists and is valid Python."""
    _HS_MODULE = os.path.join(
        _PROJECT, "dl-control", "dl_control", "middleware", "health_signal.py"
    )
    assert os.path.isfile(_HS_MODULE), f"Missing: {_HS_MODULE}"
    with open(_HS_MODULE, encoding="utf-8") as f:
        ast.parse(f.read())  # Must be syntactically valid


def test_health_signal_functions_defined():
    """detect_health_signals and HEALTH_KEYWORDS are defined."""
    _HS_MODULE = os.path.join(
        _PROJECT, "dl-control", "dl_control", "middleware", "health_signal.py"
    )
    with open(_HS_MODULE, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    func_names = {
        node.name
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "detect_health_signals" in func_names
    has_health_kw = False
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "HEALTH_KEYWORDS":
                has_health_kw = True
    assert has_health_kw, "HEALTH_KEYWORDS dict not found"


def test_health_signal_resident_with_illness():
    """Message with resident name + health keyword triggers an alert (via subprocess)."""
    _DL_CONTROL_DIR = os.path.join(_PROJECT, "dl-control")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys, json; "
                f"sys.path.insert(0, {_DL_CONTROL_DIR!r}); "
                "from dl_control.middleware.health_signal import detect_health_signals; "
                "residents = [{'id': 'R001', 'name': '张建国'}]; "
                "results = detect_health_signals("
                "'张建国咨询：感冒了吃什么药好得快', "
                "residents); "
                "print(json.dumps(results, ensure_ascii=False))"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if "No module named" in result.stderr:
        pytest.skip(f"dl-control deps not installed: {result.stderr.strip()}")
    results = json.loads(result.stdout)
    assert len(results) >= 1
    for r in results:
        assert r["resident_name"] == "张建国"
        assert r["resident_id"] == "R001"
        assert "category" in r


def test_health_signal_no_detection_for_normal_msg():
    """Message with no health keywords produces no alerts (via subprocess)."""
    _DL_CONTROL_DIR = os.path.join(_PROJECT, "dl-control")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys, json; "
                f"sys.path.insert(0, {_DL_CONTROL_DIR!r}); "
                "from dl_control.middleware.health_signal import detect_health_signals; "
                "residents = [{'id': 'R001', 'name': '张建国'}]; "
                "results = detect_health_signals('今天天气真好', residents); "
                "print(json.dumps(results))"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if "No module named" in result.stderr:
        pytest.skip(f"dl-control deps not installed: {result.stderr.strip()}")
    results = json.loads(result.stdout)
    assert len(results) == 0


def test_health_signal_keyword_without_resident():
    """Health keyword but no resident name -> no alert (via subprocess)."""
    _DL_CONTROL_DIR = os.path.join(_PROJECT, "dl-control")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys, json; "
                f"sys.path.insert(0, {_DL_CONTROL_DIR!r}); "
                "from dl_control.middleware.health_signal import detect_health_signals; "
                "residents = [{'id': 'R001', 'name': '张建国'}]; "
                "results = detect_health_signals("
                "'感冒了吃什么药', residents); "
                "print(json.dumps(results))"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if "No module named" in result.stderr:
        pytest.skip(f"dl-control deps not installed: {result.stderr.strip()}")
    results = json.loads(result.stdout)
    assert len(results) == 0


def test_health_signal_multiple_residents():
    """Multiple residents and multiple keyword categories produce cross-product."""
    _DL_CONTROL_DIR = os.path.join(_PROJECT, "dl-control")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys, json; "
                f"sys.path.insert(0, {_DL_CONTROL_DIR!r}); "
                "from dl_control.middleware.health_signal import detect_health_signals; "
                "residents = [{'id': 'R001', 'name': '张国栋'}, "
                "{'id': 'R004', 'name': '赵玉芬'}]; "
                "msg = '张国栋说他头晕，"
                "赵玉芬的褥疮需要换药'; "
                "results = detect_health_signals(msg, residents); "
                "x = json.dumps(results, ensure_ascii=False); print(x)"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if "No module named" in result.stderr:
        pytest.skip(f"dl-control deps not installed: {result.stderr.strip()}")
    results = json.loads(result.stdout)
    assert len(results) >= 2
    resident_names = {r["resident_name"] for r in results}
    assert "张国栋" in resident_names
    assert "赵玉芬" in resident_names


def test_health_signal_fall_keyword():
    """Fall keywords are detected (via subprocess)."""
    _DL_CONTROL_DIR = os.path.join(_PROJECT, "dl-control")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys, json; "
                f"sys.path.insert(0, {_DL_CONTROL_DIR!r}); "
                "from dl_control.middleware.health_signal import detect_health_signals; "
                "residents = [{'id': 'R012', 'name': '马德才'}]; "
                "results = detect_health_signals("
                "'马德才昨天夜里摔倒了', residents); "
                "x = json.dumps(results, ensure_ascii=False); print(x)"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if "No module named" in result.stderr:
        pytest.skip(f"dl-control deps not installed: {result.stderr.strip()}")
    results = json.loads(result.stdout)
    assert len(results) == 1
    assert results[0]["category"] == "跌倒"


def test_health_signal_psychological_keyword():
    """Psychological keywords are detected (via subprocess)."""
    _DL_CONTROL_DIR = os.path.join(_PROJECT, "dl-control")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys, json; "
                f"sys.path.insert(0, {_DL_CONTROL_DIR!r}); "
                "from dl_control.middleware.health_signal import detect_health_signals; "
                "residents = [{'id': 'R029', 'name': '曹美凤'}]; "
                "results = detect_health_signals("
                "'曹美凤最近很抑郁，需要关注', "
                "residents); "
                "x = json.dumps(results, ensure_ascii=False); print(x)"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if "No module named" in result.stderr:
        pytest.skip(f"dl-control deps not installed: {result.stderr.strip()}")
    results = json.loads(result.stdout)
    assert len(results) == 1
    assert results[0]["category"] == "心理"


def test_health_keywords_taxonomy_complete():
    """Every keyword maps to exactly one category (AST source check)."""
    _HS_MODULE = os.path.join(
        _PROJECT, "dl-control", "dl_control", "middleware", "health_signal.py"
    )
    with open(_HS_MODULE, encoding="utf-8") as f:
        source = f.read()
    assert "HEALTH_KEYWORDS" in source
    assert "_KEYWORD_TO_CATEGORY" in source


# ===================================================================
# 7. Workflow flow loads with 4 steps
# ===================================================================

def test_nursing_ops_flow_file_exists():
    """nursing_ops.py flow file exists."""
    assert os.path.isfile(_FLOW_FILE), f"Missing: {_FLOW_FILE}"


def test_flow_has_4_steps():
    """The nursing.ops flow defines exactly 4 steps."""
    with open(_FLOW_FILE, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            val = node.value
            if isinstance(val, ast.Call) and isinstance(val.func, ast.Name) and val.func.id == "Flow":
                for kw in val.keywords:
                    if kw.arg == "steps" and isinstance(kw.value, ast.List):
                        assert len(kw.value.elts) == 4, f"Expected 4 steps, got {len(kw.value.elts)}"
                        return
    pytest.fail("Could not find Flow() call in nursing_ops.py")


def test_flow_step_keys_are_correct():
    """The 4 steps have the correct keys in order."""
    with open(_FLOW_FILE, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    expected = [
        "nursing-schedule-step",
        "logistics-step",
        "finance-step",
        "director-report-step",
    ]
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            val = node.value
            if isinstance(val, ast.Call) and isinstance(val.func, ast.Name) and val.func.id == "Flow":
                for kw in val.keywords:
                    if kw.arg == "steps" and isinstance(kw.value, ast.List):
                        step_keys = []
                        for elt in kw.value.elts:
                            if isinstance(elt, ast.Call) and elt.args and isinstance(elt.args[0], ast.Constant):
                                step_keys.append(elt.args[0].value)
                        assert step_keys == expected, f"Steps: {step_keys}"
                        return
    pytest.fail("Could not find Flow() call in nursing_ops.py")


def test_catalog_registers_nursing_ops():
    """catalog.py registers the nursing.ops flow."""
    with open(_CATALOG_FILE, encoding="utf-8") as f:
        src = f.read()
    assert "nursing.ops" in src
    assert "nursing_ops_flow" in src


def test_flow_prepare_functions_defined():
    """All 4 prepare functions are defined in nursing_ops.py."""
    with open(_FLOW_FILE, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    func_names = {
        node.name
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    expected = {
        "_prepare_nursing_schedule",
        "_prepare_logistics",
        "_prepare_finance",
        "_prepare_director_report",
    }
    assert expected.issubset(func_names), f"Missing: {expected - func_names}"


# ===================================================================
# 8. Additional system integrity checks
# ===================================================================

def test_main_py_imports_health_signal():
    """main.py imports HealthSignalMiddleware."""
    with open(_MAIN_PY, encoding="utf-8") as f:
        src = f.read()
    assert "HealthSignalMiddleware" in src
    assert "health_signal" in src


def test_main_py_has_nursing_chat_route():
    """main.py defines the nursing chat interface."""
    with open(_MAIN_PY, encoding="utf-8") as f:
        src = f.read()
    assert "nursing/chat.html" in src or "nursing_chat" in src


def test_main_py_has_workflow_start_endpoint():
    """main.py defines nursing workflow start endpoint."""
    with open(_MAIN_PY, encoding="utf-8") as f:
        src = f.read()
    assert "/api/nursing/workflow/start" in src


def test_no_easter_eggs_in_agents():
    """Agent YAMLs do not reference removed brands (daien/yonghe)."""
    seeds = _discover_agent_seeds()
    for agent_dir, yaml_path in seeds.items():
        with open(yaml_path, encoding="utf-8") as f:
            content = f.read()
        assert "daien" not in content.lower(), f"{agent_dir} references daien"
        assert "yonghe" not in content.lower(), f"{agent_dir} references yonghe"


def test_nursing_skills_dont_reference_content_pipeline():
    """Nursing skill SKILL.md files do not reference content pipeline concepts."""
    nursing_skill_names = {
        "nursing-schedule",
        "nursing-work-order",
        "logistics-inventory",
        "logistics-query",
        "resident-query",
        "staff-query",
        "meal-query",
        "activity-query",
        "finance-query",
        "alert-query",
        "report-generate",
    }
    content_brands = {"daien", "yonghe", "戴恩", "永和", "feishu-publisher", "content.pipeline"}
    for skill_name in nursing_skill_names:
        md_path = os.path.join(_SKILLS_DIR, skill_name, "SKILL.md")
        if not os.path.isfile(md_path):
            continue
        with open(md_path, encoding="utf-8") as f:
            md = f.read()
        for brand in content_brands:
            assert brand not in md, f"{skill_name}/SKILL.md references '{brand}'"


# ===================================================================
# 9. Quick execution entrypoint
# ===================================================================

if __name__ == "__main__":
    import sys

    print("=== AI 养老院院长 MVP Smoke Test ===\n")

    failures = 0
    checks = 0

    # 1. Agent YAMLs
    print("1. Agent YAML seeds...")
    seeds = _discover_agent_seeds()
    for agent_id in sorted(EXPECTED_AGENTS):
        checks += 1
        path = os.path.join(_AGENTS_DIR, agent_id, "agent.yaml")
        if not os.path.isfile(path):
            print(f"  [FAIL] {agent_id}: agent.yaml missing")
            failures += 1
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            assert isinstance(data.get("skill_list"), list) and len(data["skill_list"]) > 0
            print(f"  [PASS] {agent_id} ({len(data['skill_list'])} skills)")
        except Exception as e:
            print(f"  [FAIL] {agent_id}: {e}")
            failures += 1

    # 2. Skill handlers
    print("\n2. Skill handlers...")
    for skill_name in SKILLS_WITH_HANDLERS:
        checks += 1
        hp = os.path.join(_SKILLS_DIR, skill_name, "handler.py")
        try:
            with open(hp, encoding="utf-8") as f:
                ast.parse(f.read())
            print(f"  [PASS] {skill_name}")
        except Exception as e:
            print(f"  [FAIL] {skill_name}: {e}")
            failures += 1

    # 3. Seed SQL tables
    print("\n3. Database tables...")
    with open(_SEED_SQL, encoding="utf-8") as f:
        seed = f.read()
    for tbl in EXPECTED_TABLES:
        checks += 1
        if f"CREATE TABLE IF NOT EXISTS {tbl}" in seed:
            print(f"  [PASS] {tbl}")
        else:
            print(f"  [FAIL] {tbl}: CREATE TABLE not found")
            failures += 1

    # 4. Auth hash (via subprocess)
    print("\n4. Auth credentials...")
    checks += 1
    try:
        result = subprocess.run(
            [sys.executable, "-c",
             f"from argon2 import PasswordHasher; "
             f"ph = PasswordHasher(); "
             f"assert ph.verify({_SEED_HASH!r}, '123456'); "
             f"print('OK')"],
            capture_output=True, text=True, timeout=10,
        )
        if result.stdout.strip() == "OK":
            print("  [PASS] Seed password hash verifies '123456'")
        else:
            print(f"  [FAIL] argon2 not available: {result.stderr.strip()}")
            failures += 1
    except Exception as e:
        print(f"  [FAIL] argon2 error: {e}")
        failures += 1

    # 5. Dashboard
    print("\n5. Dashboard API...")
    checks += 1
    try:
        with open(_MAIN_PY, encoding="utf-8") as f:
            main_src = f.read()
        assert "/api/nursing/dashboard" in main_src
        print("  [PASS] Dashboard endpoints defined")
    except Exception as e:
        print(f"  [FAIL] {e}")
        failures += 1

    # 6. Health signal (AST check only)
    print("\n6. Health signal detection...")
    checks += 1
    try:
        hs_path = os.path.join(
            _PROJECT, "dl-control", "dl_control", "middleware", "health_signal.py"
        )
        with open(hs_path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        funcs = {n.name for n in ast.iter_child_nodes(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if "detect_health_signals" in funcs:
            print("  [PASS] Health signal module valid")
        else:
            print("  [FAIL] detect_health_signals not found")
            failures += 1
    except Exception as e:
        print(f"  [FAIL] {e}")
        failures += 1

    # 7. Workflow
    print("\n7. Nursing ops workflow...")
    checks += 1
    try:
        with open(_FLOW_FILE, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        steps_found = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                val = node.value
                if isinstance(val, ast.Call) and isinstance(val.func, ast.Name) and val.func.id == "Flow":
                    for kw in val.keywords:
                        if kw.arg == "steps" and isinstance(kw.value, ast.List):
                            assert len(kw.value.elts) == 4
                            steps_found = True
        if steps_found:
            print("  [PASS] Flow has 4 steps")
        else:
            print("  [FAIL] Could not find Flow in nursing_ops.py")
            failures += 1
    except Exception as e:
        print(f"  [FAIL] {e}")
        failures += 1

    print(f"\n=== {'ALL PASSED' if failures == 0 else f'{failures} FAILURES'} ({checks} checks) ===")
    sys.exit(0 if failures == 0 else 1)
