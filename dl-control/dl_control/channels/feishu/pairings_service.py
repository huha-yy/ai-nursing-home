"""Pairings service — approve / revoke / tombstone-delete (spec §7.4-§7.6).

Every mutation bumps pairing_version and enqueues the agent for reconciler
projection.
"""

from __future__ import annotations

from uuid import UUID

from dl_control.channels.feishu.pending_reader import PendingRequest
from dl_control.channels.feishu.reconciler import ReconcilerState
from dl_control.channels.normalize import normalize_feishu_sender


class PendingRequestNotFoundError(Exception):
    pass


class TombstoneExistsError(Exception):
    def __init__(self, pairing_id: str):
        super().__init__(f"Tombstone exists for pairing {pairing_id}")
        self.pairing_id = pairing_id


class AccountMismatchError(Exception):
    def __init__(self, pending_account: str, agent_account: str):
        super().__init__(
            f"Pending account '{pending_account}' does not match agent account '{agent_account}'"
        )


async def _agent_feishu_account(tx, agent_id: UUID) -> str | None:
    cur = await tx.execute(
        "SELECT channel_config -> 'feishu' ->> 'account_id' FROM agents WHERE id = %s",
        (str(agent_id),),
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def approve_pairing(
    db,
    agent_id: UUID,
    *,
    pending: PendingRequest,
    admin_user_id: UUID,
    reconciler_state: ReconcilerState,
) -> str:
    """Approve a pending request; insert into pairings; enqueue projection."""
    sender_id_norm = normalize_feishu_sender(pending.sender_id)

    async with db.conn(user_id=str(admin_user_id), role="admin") as conn:
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"feishu_pairing:{agent_id}",),
        )

        agent_account = await _agent_feishu_account(conn, agent_id)
        if pending.account_id != agent_account:
            raise AccountMismatchError(pending.account_id, agent_account or "(none)")

        cur = await conn.execute(
            "SELECT id, status FROM pairings "
            "WHERE agent_id = %s AND account_id = %s AND sender_id_normalized = %s",
            (str(agent_id), pending.account_id, sender_id_norm),
        )
        existing = await cur.fetchone()
        if existing and existing[1] == "revoked":
            raise TombstoneExistsError(existing[0])
        if existing and existing[1] == "approved":
            return existing[0]

        cur = await conn.execute(
            "INSERT INTO pairings (agent_id, channel, account_id, "
            "    sender_id_raw, sender_id_normalized, sender_name, "
            "    status, approved_by, approved_at) "
            "VALUES (%s, 'feishu', %s, %s, %s, %s, 'approved', %s, now()) "
            "RETURNING id",
            (
                str(agent_id),
                pending.account_id,
                pending.sender_id,
                sender_id_norm,
                pending.sender_name,
                str(admin_user_id),
            ),
        )
        new_id = (await cur.fetchone())[0]

        await conn.execute(
            "UPDATE agents SET pairing_version = pairing_version + 1 WHERE id = %s",
            (str(agent_id),),
        )

        from dl_control.audit.service import write_event

        await write_event(
            conn,
            actor_user_id=str(admin_user_id),
            action="feishu_pairing.approve",
            target=str(agent_id),
            meta={"pairing_id": str(new_id)},
        )

    await reconciler_state.enqueue(agent_id)
    return str(new_id)


async def revoke_pairing(
    db,
    pairing_id: UUID,
    *,
    admin_user_id: UUID,
    reconciler_state: ReconcilerState,
) -> UUID:
    """Revoke an approved pairing (D-P3-13 tombstone)."""
    async with db.conn(user_id=str(admin_user_id), role="admin") as conn:
        cur = await conn.execute(
            "SELECT agent_id FROM pairings WHERE id = %s FOR UPDATE",
            (str(pairing_id),),
        )
        row = await cur.fetchone()
        if row is None:
            raise PendingRequestNotFoundError()
        agent_id = row[0]

        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"feishu_pairing:{agent_id}",),
        )

        cur = await conn.execute(
            "UPDATE pairings SET status = 'revoked', "
            "    revoked_by = %s, revoked_at = now() "
            "WHERE id = %s AND status = 'approved' "
            "RETURNING agent_id",
            (str(admin_user_id), str(pairing_id)),
        )
        updated = await cur.fetchone()
        if updated is None:
            raise PendingRequestNotFoundError()

        await conn.execute(
            "UPDATE agents SET pairing_version = pairing_version + 1 WHERE id = %s",
            (str(updated[0]),),
        )

        from dl_control.audit.service import write_event

        await write_event(
            conn,
            actor_user_id=str(admin_user_id),
            action="feishu_pairing.revoke",
            target=str(pairing_id),
            meta={},
        )

    await reconciler_state.enqueue(updated[0])
    return pairing_id


async def delete_tombstone(
    db,
    pairing_id: UUID,
    *,
    admin_user_id: UUID,
    reconciler_state: ReconcilerState,
) -> None:
    """Delete a tombstone row (D-P3-13)."""
    async with db.conn(user_id=str(admin_user_id), role="admin") as conn:
        cur = await conn.execute(
            "SELECT agent_id FROM pairings WHERE id = %s AND status = 'revoked'",
            (str(pairing_id),),
        )
        row = await cur.fetchone()
        if row is None:
            raise PendingRequestNotFoundError()
        agent_id = row[0]

        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"feishu_pairing:{agent_id}",),
        )

        await conn.execute(
            "DELETE FROM pairings WHERE id = %s AND status = 'revoked'",
            (str(pairing_id),),
        )

        await conn.execute(
            "UPDATE agents SET pairing_version = pairing_version + 1 WHERE id = %s",
            (str(agent_id),),
        )

        from dl_control.audit.service import write_event

        await write_event(
            conn,
            actor_user_id=str(admin_user_id),
            action="feishu_pairing.delete_tombstone",
            target=str(pairing_id),
            meta={},
        )

    await reconciler_state.enqueue(agent_id)
