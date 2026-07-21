# Task 3 Report: Nursing User Auth with Role/Dept/Building Context Injection

**Status:** Complete
**Date:** 2026-07-21

## Summary

Modified the authentication system to use the new `nursing_users` table (created in Task 2), added a dedicated nursing login endpoint, extended session data with nursing context fields, added context-injection middleware, and updated agent provisioning to inject nursing environment variables.

## Changes

### 1. Auth service (`dl_control/auth/service.py`)
- Added `NursingLoginResult` dataclass with fields: `user_id`, `username`, `name`, `role`, `dept`, `building`, `floor`
- Added `try_nursing_login()` async function that queries `nursing_users` table, verifies password with argon2id, applies the same rate-limiting logic as the admin login, and returns a `NursingLoginResult`
- Uses the same `LoginError` exception class for consistency (rate_limited, missing_user, password_mismatch)
- Audit events use `nursing_login_failed` / `nursing_login_succeeded` actions (distinct from admin login events)

### 2. Session store (`dl_control/auth/sessions.py`)
- Extended `Session` dataclass with optional nursing context fields: `name`, `dept`, `building`, `floor`, `username` (all default to `None` for backward compatibility)
- `SessionStore.create()` now accepts optional `name`, `dept`, `building`, `floor`, `username` params and stores them in the Redis hash
- `SessionStore.load()` reads these optional fields via `.get()` (returns `None` for old sessions that lack them)

### 3. Auth routes (`dl_control/auth/routes.py`)
- Added `POST /auth/nursing-login` endpoint (Option A: clean separation from `/login`)
- Accepts `username` and `password` as form data
- Creates a Redis session with full nursing context (user_id, username, name, role, dept, building, floor)
- Returns a `dato_session` cookie + 302 redirect to `/admin`
- On failure, returns the login page with a generic error (401)

### 4. Auth middleware (`dl_control/auth/middleware.py`)
- Added `NursingContext` dataclass with fields: `user_id`, `username`, `name`, `role`, `dept`, `building`, `floor`, `is_nursing`
- Added `inject_nursing_context(store)` FastAPI dependency that:
  - Resolves the session cookie
  - Checks if the session role is one of the 6 nursing roles (`director`, `nursing_dept`, `logistics_dept`, `building`, `floor`, `general`)
  - If nursing: populates `request.state.nursing_context` with all context fields
  - If non-nursing or no session: sets `request.state.nursing_context` with `is_nursing=False`

### 5. Agent provisioning config (`dl_control/agents/provisioning/config_gen.py`)
- Added optional `nursing_role`, `nursing_dept`, `nursing_building`, `nursing_floor` parameters to `render_env_file()` (all default to `""`)
- Emits `NURSING_ROLE`, `NURSING_DEPT`, `NURSING_BUILDING`, `NURSING_FLOOR` env vars in every generated `.env` file
- Default empty values ensure backward compatibility; callers can inject specific values when context is available

### 6. Verification test (`tests/test_nursing_auth.py`)
- Unit tests for: argon2id seed hash verification, `NursingLoginResult` fields, `Session` nursing fields, `NursingContext` defaults, `render_env_file` nursing vars output, sh_single_quote
- Integration test skeleton (skip by default, requires `--run-nursing-integration` flag and running full stack)
- Standalone `__main__` verification script that can be run directly

## Files Changed

| File | Change |
|------|--------|
| `dl-control/dl_control/auth/service.py` | Added `NursingLoginResult`, `try_nursing_login()` |
| `dl-control/dl_control/auth/sessions.py` | Extended `Session` with nursing fields, updated `create`/`load` |
| `dl-control/dl_control/auth/routes.py` | Added `POST /auth/nursing-login` |
| `dl-control/dl_control/auth/middleware.py` | Added `NursingContext`, `inject_nursing_context()` |
| `dl-control/dl_control/agents/provisioning/config_gen.py` | Added nursing env var params to `render_env_file()` |
| `tests/test_nursing_auth.py` | New verification test (unit + integration skeleton) |

## Files NOT Changed (no changes needed)

- `dl-control/dl_control/main.py` — The nursing-login route is inside the existing `auth_routes.make_router()` which is already registered. The nursing middleware is opt-in per route via `Depends` — no global registration needed.
- `dl-control/dl_control/agents/provisioning/service.py` — The call to `render_env_file()` passes keyword arguments; the new params have defaults, so the existing call works unchanged.

## Backward Compatibility

- Existing `/login` and `/logout` endpoints are unchanged
- `Session` fields are optional with `None` defaults — old Redis sessions still load correctly
- `SessionStore.create()` with only the original params works unchanged
- `render_env_file()` defaults to empty nursing vars — existing provisioning flows are unaffected

## Verification

All 5 unit-level checks pass:
```
[PASS] 1. Seed password hash verifies '123456'
[PASS] 2. NursingLoginResult captures role='director'
[PASS] 3. Session supports nursing name/username fields
[PASS] 4. render_env_file injects NURSING_* env vars
[PASS] 5. NursingContext middleware dataclass works
```

Ruff linting passes with zero errors on all changed files.
