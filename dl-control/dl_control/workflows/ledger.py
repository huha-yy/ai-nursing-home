"""side_effect_ledger — the durability keystone (spec §5.4, W8).

Every external mutation goes through ledgered_call. Ledger writes commit in
their OWN transactions: 'started' must be durable before perform() runs, and
must survive the surrounding step failing or rolling back. The helper raises
(it never writes workflow_run): the runner converts UnresolvedEffectError into
the waiting_manual park (plan decision D-P13B-7).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from dl_control.db import Database
from dl_control.workflows.errors import (
    KnownCleanError,
    LedgerConflictError,
    UnresolvedEffectError,
)


def request_hash(request: dict[str, Any]) -> str:
    """sha256 over the canonical (sorted-key, compact) JSON of the request."""
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def ledgered_call(
    db: Database,
    *,
    key: str,
    run_id: UUID,
    step_key: str,
    attempt: int,
    target: str,
    request: dict[str, Any],
    perform,
) -> Any:
    """Execute one external mutation under the three-state ledger contract.

    Raises LedgerConflictError (key reuse with a different request),
    UnresolvedEffectError (a prior attempt may have fired, or perform failed
    ambiguously — park in waiting_manual), or re-raises KnownCleanError from
    perform (retryable under the step's retry policy).
    """
    rhash = request_hash(request)
    async with db.conn(user_id=None, role="system") as conn:
        cur = await conn.execute(
            "INSERT INTO side_effect_ledger "
            "(idempotency_key, run_id, step_key, attempt, target, request_hash) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (idempotency_key) DO NOTHING",
            (key, run_id, step_key, attempt, target, rhash),
        )
        fresh_insert = cur.rowcount == 1
        if not fresh_insert:
            cur = await conn.execute(
                "SELECT status, request_hash, response FROM side_effect_ledger "
                "WHERE idempotency_key = %s",
                (key,),
            )
            status, existing_hash, response = await cur.fetchone()
            if existing_hash != rhash:
                raise LedgerConflictError(
                    f"{key}: request_hash changed for an existing ledger entry"
                )
            if status == "committed":
                return response  # skip the call, reuse the stored result
            if status == "started":
                # PRE-EXISTING started — a prior attempt may have fired (§5.4).
                raise UnresolvedEffectError(f"{key}: prior attempt unresolved")
            # status == 'failed' — known-clean retry. Re-arm to 'started'
            # atomically BEFORE performing, so a crash mid-retry recovers as
            # unresolved 'started' (manual confirm), never as a 'failed' row
            # that silently auto-retries again (spec R5 P1).
            cur = await conn.execute(
                "UPDATE side_effect_ledger SET status = 'started', attempt = %s, "
                "updated_at = now() "
                "WHERE idempotency_key = %s AND status = 'failed'",
                (attempt, key),
            )
            if cur.rowcount != 1:
                # Raced with a concurrent transition — never risk a double-fire.
                raise UnresolvedEffectError(f"{key}: concurrent ledger transition")
    # 'started' is committed and durable. Perform OUTSIDE any transaction.
    try:
        resp = await perform(request)
    except KnownCleanError:
        async with db.conn(user_id=None, role="system") as conn:
            await conn.execute(
                "UPDATE side_effect_ledger SET status = 'failed', updated_at = now() "
                "WHERE idempotency_key = %s AND status = 'started'",
                (key,),
            )
        raise  # the step retry policy applies
    except Exception as exc:
        # AMBIGUOUS — the provider may have acted. Row stays 'started';
        # the runner parks the run in waiting_manual. NO auto-retry (§5.4).
        raise UnresolvedEffectError(f"{key}: ambiguous failure: {exc}") from exc
    try:
        async with db.conn(user_id=None, role="system") as conn:
            await conn.execute(
                "UPDATE side_effect_ledger SET status = 'committed', response = %s, "
                "updated_at = now() WHERE idempotency_key = %s",
                (Jsonb(resp), key),
            )
    except Exception as exc:
        # The effect FIRED but recording 'committed' failed (serialization or
        # DB error) — the row stays 'started'. Surface as unresolved (manual
        # confirm); a retryable classification would re-fire the effect.
        raise UnresolvedEffectError(f"{key}: effect fired but ledger commit failed: {exc}") from exc
    return resp
