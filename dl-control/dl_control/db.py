"""Database connection pool + GUC-setting transaction context manager.

The app connects as the non-owner role dl_control_app (its DSN names that
role directly — no rewriting), so RLS policies apply. Every query runs in a
transaction with two GUCs set via SET LOCAL semantics:

  app.current_user_id — UUID of the acting user, or '' if absent
  app.current_role    — 'admin' | 'user' | 'system'
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import psycopg
from psycopg.rows import tuple_row
from psycopg_pool import AsyncConnectionPool

Role = Literal["admin", "user", "system"]


class Database:
    """Owns the psycopg async pool. Lifespan-managed in main.py."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: AsyncConnectionPool | None = None

    async def connect(self) -> None:
        """Open the pool. The DSN already names dl_control_app (spec §5.1)."""
        self._pool = AsyncConnectionPool(conninfo=self._dsn, min_size=1, max_size=10, open=False)
        await self._pool.open()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def conn(
        self,
        *,
        user_id: str | None,
        role: Role,
    ) -> AsyncIterator[psycopg.AsyncConnection]:
        """Yield an AsyncConnection with GUCs set inside a transaction.
        Commits on clean exit; rolls back on exception."""
        if self._pool is None:
            raise RuntimeError("Database is not connected; call .connect() first")
        async with self._pool.connection() as conn:
            conn.row_factory = tuple_row
            async with conn.transaction():
                # set_config(..., true) == SET LOCAL — transaction-scoped,
                # auto-clears on commit/rollback.
                await conn.execute(
                    "SELECT set_config('app.current_user_id', %s, true)",
                    (user_id or "",),
                )
                await conn.execute(
                    "SELECT set_config('app.current_role', %s, true)",
                    (role,),
                )
                await conn.execute("SET TIME ZONE 'Asia/Shanghai'")
                yield conn
