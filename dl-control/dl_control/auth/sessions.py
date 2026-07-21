"""Redis-backed session store with itsdangerous-signed cookies.

A session is a Redis hash at sess:<sid> with a TTL. The cookie carries the
sid signed by URLSafeTimedSerializer; a per-user set user_sessions:<uid>
indexes sids for bulk invalidation on password change (spec §6.4).
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from itsdangerous import BadSignature, URLSafeTimedSerializer
from redis.asyncio import Redis

_COOKIE_SALT = "dato-session-cookie"


@dataclass(frozen=True, slots=True)
class Session:
    sid: str
    user_id: str
    role: str
    created_at: int
    ip: str
    ua_fingerprint: str
    csrf_token: str


def _sess_key(sid: str) -> str:
    return f"sess:{sid}"


def _user_key(user_id: str) -> str:
    return f"user_sessions:{user_id}"


class SessionStore:
    def __init__(self, redis: Redis, *, ttl_seconds: int, secret_key: str) -> None:
        self._r = redis
        self._ttl = ttl_seconds
        self._serializer = URLSafeTimedSerializer(secret_key, salt=_COOKIE_SALT)

    # -- cookie codec --------------------------------------------------
    def sign(self, sid: str) -> str:
        """Sign a sid for the cookie value."""
        return self._serializer.dumps(sid)

    def unsign(self, token: str) -> str | None:
        """Return the sid, or None if the cookie is forged."""
        try:
            return self._serializer.loads(token)
        except BadSignature:
            return None

    # -- session lifecycle --------------------------------------------
    async def create(self, *, user_id: str, role: str, ip: str, ua_fingerprint: str) -> Session:
        sid = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        created = int(time.time())
        async with self._r.pipeline(transaction=True) as pipe:
            key = _sess_key(sid)
            pipe.hset(
                key,
                mapping={
                    "user_id": user_id,
                    "role": role,
                    "created_at": str(created),
                    "ip": ip,
                    "ua_fingerprint": ua_fingerprint,
                    "csrf_token": csrf,
                },
            )
            pipe.expire(key, self._ttl)
            pipe.sadd(_user_key(user_id), sid)
            pipe.expire(_user_key(user_id), self._ttl)
            await pipe.execute()
        return Session(
            sid=sid,
            user_id=user_id,
            role=role,
            created_at=created,
            ip=ip,
            ua_fingerprint=ua_fingerprint,
            csrf_token=csrf,
        )

    async def load(self, sid: str) -> Session | None:
        if not sid:
            return None
        data = await self._r.hgetall(_sess_key(sid))
        if not data:
            return None
        return Session(
            sid=sid,
            user_id=data["user_id"],
            role=data["role"],
            created_at=int(data["created_at"]),
            ip=data["ip"],
            ua_fingerprint=data["ua_fingerprint"],
            csrf_token=data["csrf_token"],
        )

    async def renew(self, sid: str) -> None:
        """Slide the TTL on the session and its per-user index entry."""
        sess = await self.load(sid)
        if sess is None:
            return
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.expire(_sess_key(sid), self._ttl)
            pipe.expire(_user_key(sess.user_id), self._ttl)
            await pipe.execute()

    async def delete(self, sid: str) -> None:
        sess = await self.load(sid)
        await self._r.delete(_sess_key(sid))
        if sess is not None:
            await self._r.srem(_user_key(sess.user_id), sid)

    async def delete_all_for_user(self, user_id: str) -> int:
        """Drop every session for a user. Returns the count removed."""
        sids = await self._r.smembers(_user_key(user_id))
        if sids:
            await self._r.delete(*(_sess_key(s) for s in sids))
        await self._r.delete(_user_key(user_id))
        return len(sids)
