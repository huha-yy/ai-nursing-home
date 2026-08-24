"""Auth service: argon2id hashing + the login flow.

No HTTP here. Audit rows are written inside the same DB transaction that
observed the state they describe (spec §6.3).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx
from argon2 import PasswordHasher
from redis.asyncio import Redis

from dl_control.audit.service import write_event
from dl_control.db import Database

_ph = PasswordHasher()
# Constant dummy hash so missing-user logins still pay the argon2 cost and
# stay timing-indistinguishable from real users.
_DUMMY_HASH = _ph.hash("dummy-password-for-constant-time-compare")


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class LoginResult:
    user_id: str
    role: str


class LoginError(RuntimeError):
    """Raised on any login failure. `reason` is internal — the HTTP layer
    shows a generic message and never reveals which condition failed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _username_key(username: str) -> str:
    return f"login_fail:username:{username}"


def _ip_key(ip: str) -> str:
    return f"login_fail:ip:{ip}"


async def _over_limit(redis: Redis, *, username: str, ip: str, fails: int) -> bool:
    user_count, ip_count = await redis.mget(_username_key(username), _ip_key(ip))
    return int(user_count or 0) > fails or int(ip_count or 0) > fails


async def _bump_counters(redis: Redis, *, username: str, ip: str, window: int) -> None:
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(_username_key(username))
        pipe.expire(_username_key(username), window)
        pipe.incr(_ip_key(ip))
        pipe.expire(_ip_key(ip), window)
        await pipe.execute()


async def _reset_counters(redis: Redis, *, username: str, ip: str) -> None:
    await redis.delete(_username_key(username), _ip_key(ip))


async def try_login(
    db: Database,
    redis: Redis,
    *,
    username: str,
    password: str,
    ip: str,
    rate_limit_fails: int,
    rate_limit_window: int,
) -> LoginResult:
    """Validate credentials; return a LoginResult or raise LoginError."""
    # 1. Pre-hash lockout gate — no argon2 runs if already locked (spec §6.3).
    if await _over_limit(redis, username=username, ip=ip, fails=rate_limit_fails):
        async with db.conn(user_id=None, role="system") as conn:
            await write_event(
                conn,
                actor_user_id=None,
                action="login_failed",
                target="login",
                meta={"reason": "rate_limited", "ip": ip},
            )
        raise LoginError("rate_limited")

    # 2. Look up the user.
    async with db.conn(user_id=None, role="system") as conn:
        cur = await conn.execute(
            "SELECT id, password_hash, role, status FROM users WHERE username = %s",
            (username,),
        )
        row = await cur.fetchone()

    # 3. Missing user — dummy verify (constant-time), bump, audit, raise.
    if row is None:
        verify_password(password, _DUMMY_HASH)
        await _bump_counters(redis, username=username, ip=ip, window=rate_limit_window)
        async with db.conn(user_id=None, role="system") as conn:
            await write_event(
                conn,
                actor_user_id=None,
                action="login_failed",
                target="login",
                meta={"reason": "missing_user", "ip": ip},
            )
        raise LoginError("missing_user")

    user_id, password_hash, role, status = row
    user_id = str(user_id)

    # 4. Password mismatch.
    if not verify_password(password, password_hash):
        await _bump_counters(redis, username=username, ip=ip, window=rate_limit_window)
        async with db.conn(user_id=user_id, role="system") as conn:
            await write_event(
                conn,
                actor_user_id=user_id,
                action="login_failed",
                target="login",
                meta={"reason": "password_mismatch", "ip": ip},
            )
        raise LoginError("password_mismatch")

    # 5. Non-active status.
    if status != "active":
        async with db.conn(user_id=user_id, role="system") as conn:
            await write_event(
                conn,
                actor_user_id=user_id,
                action="login_blocked_status",
                target="login",
                meta={"reason": "blocked_status", "status": status, "ip": ip},
            )
        raise LoginError("blocked_status")

    # 6. Success.
    async with db.conn(user_id=user_id, role="system") as conn:
        await write_event(
            conn,
            actor_user_id=user_id,
            action="login_succeeded",
            target="login",
            meta={"ip": ip},
        )
    await _reset_counters(redis, username=username, ip=ip)
    return LoginResult(user_id=user_id, role=role)


@dataclass(frozen=True, slots=True)
class NursingLoginResult:
    user_id: str
    username: str
    name: str
    role: str
    dept: str | None
    building: str | None
    floor: str | None


@dataclass(frozen=True, slots=True)
class FamilyLoginResult:
    """家属登录结果 — 身份真源在 nursing-erp，本侧只缓存令牌与绑定清单。"""

    user_id: str  # "family:<id>"，与员工 u0xx 文本 ID 不撞
    username: str  # 手机号
    name: str
    token: str  # ERP FamilyMember.token，预取时经 X-Family-Token 回传
    residents: list[dict]  # [{id, name, building, room, relation}]


def _family_key(phone: str) -> str:
    return f"login_fail:family:{phone}"


async def try_family_login(
    db: Database,
    redis: Redis,
    *,
    username: str,
    password: str,
    ip: str,
    rate_limit_fails: int,
    rate_limit_window: int,
) -> FamilyLoginResult:
    """手机号+密码 → 委托 nursing-erp POST /api/family/auth/ 换令牌与绑定清单。

    家属账号不落本侧库；失败限流用家属专用 Redis 键（不与员工计数器混用，
    也天然与 ERP 侧 admin 登录的无限流姿态隔离）。
    """
    phone = username.strip()

    async def _audit(action: str, reason: str) -> None:
        async with db.conn(user_id=None, role="system") as conn:
            await write_event(
                conn,
                actor_user_id=None,
                action=action,
                target="login",
                meta={"reason": reason, "ip": ip},
            )

    # 1. 锁定门槛：超限直接拒（不再打 ERP）。
    count = await redis.get(_family_key(phone))
    if count is not None and int(count) > rate_limit_fails:
        await _audit("family_login_failed", "rate_limited")
        raise LoginError("rate_limited")

    # 2. 委托 ERP 鉴权（X-API-Key 服务间；ERP 侧校验手机号+密码+家属档案）。
    erp_url = os.environ.get("NURSING_ERP_URL", "http://192.168.10.247:9081").rstrip("/")
    erp_key = os.environ.get("NURSING_ERP_API_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{erp_url}/api/family/auth/",
                headers={"X-API-Key": erp_key} if erp_key else {},
                json={"phone": phone, "password": password},
            )
    except Exception:
        await _audit("family_login_failed", "erp_unreachable")
        raise LoginError("erp_unreachable") from None

    if resp.status_code != 200:
        await redis.incr(_family_key(phone))
        await redis.expire(_family_key(phone), rate_limit_window)
        reason = "invalid_credentials" if resp.status_code == 401 else f"erp_{resp.status_code}"
        await _audit("family_login_failed", reason)
        raise LoginError(reason)

    data = resp.json()
    await redis.delete(_family_key(phone))
    await _audit("family_login_succeeded", "ok")
    return FamilyLoginResult(
        user_id=f"family:{data['family_id']}",
        username=phone,
        name=data["name"],
        token=data["token"],
        residents=data.get("residents", []),
    )


async def try_nursing_login(
    db: Database,
    redis: Redis,
    *,
    username: str,
    password: str,
    ip: str,
    rate_limit_fails: int,
    rate_limit_window: int,
) -> NursingLoginResult:
    """Validate nursing_users credentials; return a NursingLoginResult or raise LoginError."""
    # 1. Pre-hash lockout gate — no argon2 runs if already locked.
    if await _over_limit(redis, username=username, ip=ip, fails=rate_limit_fails):
        async with db.conn(user_id=None, role="system") as conn:
            await write_event(
                conn,
                actor_user_id=None,
                action="nursing_login_failed",
                target="login",
                meta={"reason": "rate_limited", "ip": ip},
            )
        raise LoginError("rate_limited")

    # 2. Look up the nursing user.
    async with db.conn(user_id=None, role="system") as conn:
        cur = await conn.execute(
            "SELECT user_id, username, name, role, password, dept, building, floor "
            "FROM nursing_users WHERE username = %s",
            (username,),
        )
        row = await cur.fetchone()

    # 3. Missing user — dummy verify (constant-time), bump, audit, raise.
    if row is None:
        verify_password(password, _DUMMY_HASH)
        await _bump_counters(redis, username=username, ip=ip, window=rate_limit_window)
        async with db.conn(user_id=None, role="system") as conn:
            await write_event(
                conn,
                actor_user_id=None,
                action="nursing_login_failed",
                target="login",
                meta={"reason": "missing_user", "ip": ip},
            )
        raise LoginError("missing_user")

    user_id, db_username, name, role, password_hash, dept, building, floor = row
    user_id = str(user_id)
    # Nursing users have text IDs (e.g. "u001"), not UUIDs.
    # Pass None for audit so FK constraint doesn't fail; real identity is in the session.

    # 4. Password mismatch.
    if not verify_password(password, password_hash):
        await _bump_counters(redis, username=username, ip=ip, window=rate_limit_window)
        async with db.conn(user_id=None, role="system") as conn:
            await write_event(
                conn,
                actor_user_id=None,
                action="nursing_login_failed",
                target="login",
                meta={"reason": "password_mismatch", "ip": ip},
            )
        raise LoginError("password_mismatch")

    # 5. Success.
    async with db.conn(user_id=None, role="system") as conn:
        await write_event(
            conn,
            actor_user_id=None,
            action="nursing_login_succeeded",
            target="login",
            meta={"ip": ip},
        )
    await _reset_counters(redis, username=username, ip=ip)
    return NursingLoginResult(
        user_id=user_id,
        username=db_username,
        name=name,
        role=role,
        dept=dept,
        building=building,
        floor=floor,
    )
