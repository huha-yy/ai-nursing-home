"""Auth service: argon2id hashing + the login flow.

No HTTP here. Audit rows are written inside the same DB transaction that
observed the state they describe (spec §6.3).
"""

from __future__ import annotations

from dataclasses import dataclass

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
