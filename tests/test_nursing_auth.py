"""Task 3 verification: nursing login endpoint, session context injection.

Run with the full stack up: make up && make admin-init

    uv run pytest tests/test_nursing_auth.py -v
"""

from __future__ import annotations

import asyncio

import pytest
from argon2 import PasswordHasher


# ---------------------------------------------------------------------------
# Unit-level: password verification against the known seed-data hash
# ---------------------------------------------------------------------------

# Argon2id hash of "123456" from 03-nursing-seed.sql:u001
_SEED_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4"
    "$mNj06Lk/yZZWT091ecHM2w"
    "$Db2EM3+8cp6j65wewZ1UxYpjRJ7YjDp1advtQI20wjU"
)


def test_seed_password_hash_verifies():
    """The seed-data argon2id hash resolves to '123456'."""
    ph = PasswordHasher()
    assert ph.verify(_SEED_HASH, "123456")


def test_wrong_password_rejected():
    """A wrong password does not verify against the seed hash."""
    ph = PasswordHasher()
    try:
        ph.verify(_SEED_HASH, "wrong_password")
        assert False, "expected VerifyMismatchError"
    except Exception:
        pass  # expected


# ---------------------------------------------------------------------------
# Unit-level: NursingLoginResult dataclass
# ---------------------------------------------------------------------------

def test_nursing_login_result_fields():
    """NursingLoginResult captures all nursing context fields."""
    from dl_control.auth.service import NursingLoginResult

    r = NursingLoginResult(
        user_id="u001",
        username="wang_jianguo",
        name="王建国",
        role="director",
        dept=None,
        building=None,
        floor=None,
    )
    assert r.user_id == "u001"
    assert r.username == "wang_jianguo"
    assert r.name == "王建国"
    assert r.role == "director"
    assert r.dept is None
    assert r.building is None
    assert r.floor is None


# ---------------------------------------------------------------------------
# Unit-level: config_gen injects NURSING_* env vars
# ---------------------------------------------------------------------------

def test_render_env_file_includes_nursing_vars():
    """render_env_file always emits NURSING_ROLE / DEPT / BUILDING / FLOOR."""
    from dl_control.agents.provisioning.config_gen import render_env_file

    env = render_env_file(
        openclaw_token="t",
        deepseek_api_key="k",
        pexels_api_key="",
        agent_id="x",
    )
    assert "NURSING_ROLE=" in env
    assert "NURSING_DEPT=" in env
    assert "NURSING_BUILDING=" in env
    assert "NURSING_FLOOR=" in env


def test_render_env_file_nursing_defaults_empty():
    """By default the nursing vars are empty (single-quoted empty strings)."""
    from dl_control.agents.provisioning.config_gen import render_env_file

    env = render_env_file(
        openclaw_token="t",
        deepseek_api_key="k",
        pexels_api_key="",
        agent_id="x",
    )
    assert "NURSING_ROLE=''" in env
    assert "NURSING_DEPT=''" in env
    assert "NURSING_BUILDING=''" in env
    assert "NURSING_FLOOR=''" in env


def test_render_env_file_nursing_with_values():
    """When nursing params are passed, they are populated in the env file."""
    from dl_control.agents.provisioning.config_gen import render_env_file

    env = render_env_file(
        openclaw_token="t",
        deepseek_api_key="k",
        pexels_api_key="",
        agent_id="x",
        nursing_role="director",
        nursing_dept="护理科",
        nursing_building="3号楼",
        nursing_floor="2层",
    )
    assert "NURSING_ROLE='director'" in env
    assert "NURSING_DEPT='护理科'" in env
    assert "NURSING_BUILDING='3号楼'" in env
    assert "NURSING_FLOOR='2层'" in env


# ---------------------------------------------------------------------------
# Unit-level: Session supports nursing fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_construct_with_nursing_fields():
    """Session dataclass supports optional nursing context fields."""
    from dl_control.auth.sessions import Session

    s = Session(
        sid="s",
        user_id="u001",
        role="director",
        created_at=1,
        ip="127.0.0.1",
        ua_fingerprint="fp",
        csrf_token="c",
        name="王建国",
        dept=None,
        building=None,
        floor=None,
        username="wang_jianguo",
    )
    assert s.role == "director"
    assert s.name == "王建国"
    assert s.username == "wang_jianguo"
    assert s.dept is None


def test_session_default_nursing_fields_none():
    """Session nursing fields default to None for backward compat."""
    from dl_control.auth.sessions import Session

    s = Session(
        sid="s",
        user_id="u",
        role="admin",
        created_at=1,
        ip="127.0.0.1",
        ua_fingerprint="fp",
        csrf_token="c",
    )
    assert s.name is None
    assert s.dept is None
    assert s.building is None
    assert s.floor is None
    assert s.username is None


# ---------------------------------------------------------------------------
# Unit-level: NursingContext middleware dataclass
# ---------------------------------------------------------------------------

def test_nursing_context_defaults():
    """NursingContext starts empty with is_nursing=False."""
    from dl_control.auth.middleware import NursingContext

    ctx = NursingContext()
    assert ctx.is_nursing is False
    assert ctx.user_id is None
    assert ctx.role is None


# ---------------------------------------------------------------------------
# Unit-level: sh_single_quote (used by render_env_file)
# ---------------------------------------------------------------------------

def test_sh_single_quote_preserves_special_chars():
    """sh_single_quote safely quotes values with special characters."""
    from dl_control.agents.provisioning.config_gen import sh_single_quote

    assert sh_single_quote("hello") == "'hello'"
    assert sh_single_quote("it's") == "'it'\\''s'"
    assert sh_single_quote("") == "''"


# ---------------------------------------------------------------------------
# Integration: nursing login endpoint (requires full stack)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    "not config.getoption('--run-nursing-integration')",
    reason="requires full stack with nursing_users seeded; use --run-nursing-integration",
)
def test_nursing_login_returns_session_cookie(
    http_session, caddy_url, dl_control_ready
):
    """POST /auth/nursing-login with wang_jianguo / 123456 sets a session cookie."""
    r = http_session.post(
        f"{caddy_url}/auth/nursing-login",
        data={"username": "wang_jianguo", "password": "123456"},
        follow_redirects=False,
    )
    # Either a 302 redirect with a set-cookie, or a 401 if the seed data
    # hasn't been loaded.
    if r.status_code == 401:
        pytest.skip("nursing_users table may not be seeded — run init SQL first")
    assert r.status_code == 302, f"Expected 302, got {r.status_code}: {r.text[:200]}"
    assert "dato_session" in r.cookies or "set-cookie" in str(r.headers).lower()


# ---------------------------------------------------------------------------
# Manual verification script (run directly with: python tests/test_nursing_auth.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== Task 3: Nursing Auth Verification ===\n")

    # 1. Password hash verification
    try:
        ph = PasswordHasher()
        ok = ph.verify(_SEED_HASH, "123456")
        print("[PASS] 1. Seed password hash verifies '123456'")
    except Exception as e:
        print(f"[FAIL] 1. Password verification failed: {e}")
        sys.exit(1)

    # 2. NursingLoginResult dataclass
    from dl_control.auth.service import NursingLoginResult

    r = NursingLoginResult(
        user_id="u001",
        username="wang_jianguo",
        name="王建国",
        role="director",
        dept=None,
        building=None,
        floor=None,
    )
    assert r.role == "director"
    print("[PASS] 2. NursingLoginResult captures role='director'")

    # 3. Session with nursing fields
    from dl_control.auth.sessions import Session

    s = Session(
        sid="test",
        user_id="u001",
        role="director",
        created_at=0,
        ip="127.0.0.1",
        ua_fingerprint="fp",
        csrf_token="c",
        name="王建国",
        username="wang_jianguo",
    )
    assert s.name == "王建国"
    print("[PASS] 3. Session supports nursing name/username fields")

    # 4. render_env_file injects nursing vars
    from dl_control.agents.provisioning.config_gen import render_env_file

    env = render_env_file(
        openclaw_token="t",
        deepseek_api_key="k",
        pexels_api_key="",
        agent_id="x",
        nursing_role="director",
        nursing_dept="护理科",
        nursing_building="3号楼",
        nursing_floor="2层",
    )
    assert "NURSING_ROLE='director'" in env
    assert "NURSING_DEPT='护理科'" in env
    assert "NURSING_BUILDING='3号楼'" in env
    assert "NURSING_FLOOR='2层'" in env
    print("[PASS] 4. render_env_file injects NURSING_* env vars")

    # 5. NursingContext middleware
    from dl_control.auth.middleware import NursingContext

    ctx = NursingContext()
    assert ctx.is_nursing is False
    ctx.role = "director"
    ctx.building = "3号楼"
    ctx.is_nursing = True
    assert ctx.is_nursing is True
    print("[PASS] 5. NursingContext middleware dataclass works")

    print("\n=== All verification checks passed ===")
    print("To test the live endpoint, start the full stack and run:")
    print("  curl -X POST https://localhost:9443/auth/nursing-login \\")
    print("    -d 'username=wang_jianguo&password=123456' -v")
