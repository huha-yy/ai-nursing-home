"""Integration tests for the nursing-ops workflow runner — state machine,
agent-call lifecycle, and step sequencing.

These tests use a fake dispatcher (no agent containers needed) but require a
running Postgres with the workflow_runner migration applied.

Run:
    PYTHONPATH=. uv run pytest tests/test_workflow_integration.py -v
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from dl_control.db import Database
from dl_control.workflows import agent_calls, config_cache, runs
from dl_control.workflows.dispatch import DispatchConfig
from dl_control.workflows.errors import (
    DuplicateActiveRunError,
    LeaseLostError,
    WorkflowDisabledError,
)
from dl_control.workflows.model import AgentTask, CallAgent, Flow, Retry, Step

# ---------------------------------------------------------------------------
# Configuration — override via env vars for CI / local
# ---------------------------------------------------------------------------
DB_URL = os.environ.get("TEST_DATABASE_URL", os.environ.get("DATABASE_URL", ""))
NEEDS_DB = pytest.mark.skipif(
    not DB_URL, reason="TEST_DATABASE_URL or DATABASE_URL not set"
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TEST_AGENT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


@pytest.fixture
async def db():
    """A fresh Database connected to the test Postgres."""
    if not DB_URL:
        pytest.skip("test database not configured")
    d = Database(dsn=DB_URL)
    await d.connect()
    yield d
    await d.close()


@pytest.fixture
def fake_flow():
    """A minimal 2-step Flow for testing the runner state machine."""
    return Flow(
        "test.flow",
        "1.0.0",
        steps=[
            Step(
                "step-1",
                call_agent=CallAgent(
                    prepare=lambda i, o: AgentTask(
                        agent_id=_TEST_AGENT_ID, message="task for step 1"
                    ),
                    timeout_seconds=60,
                ),
                retry=Retry(max_attempts=2, base_seconds=1),
            ),
            Step(
                "step-2",
                call_agent=CallAgent(
                    prepare=lambda i, o: AgentTask(
                        agent_id=_TEST_AGENT_ID, message="task for step 2"
                    ),
                    timeout_seconds=60,
                ),
                retry=Retry(max_attempts=2, base_seconds=1),
            ),
        ],
    )


def fake_dispatcher(acked: bool = True):
    """Return an AsyncMock that returns ``acked``."""

    async def _dispatch(cfg, *, agent_id, correlation_id, run_id, step_key, message):
        return acked

    return _dispatch


# ===================================================================
# Workflow run lifecycle — start / claim / advance / finish
# ===================================================================


@NEEDS_DB
class TestWorkflowRunLifecycle:
    """Start a nursing-ops run and walk through its DB states."""

    async def test_start_run_creates_pending_row(self, db):
        """start_run creates a workflow_run row with status='pending'."""
        async with db.conn(user_id=None, role="system") as conn:
            # Ensure the workflow row exists
            await conn.execute(
                "INSERT INTO workflow (id, enabled, latest_version, display_name, default_trigger) "
                "VALUES ('test.start_run', true, '1.0.0', 'Test', 'manual') "
                "ON CONFLICT (id) DO UPDATE SET enabled = true"
            )
            await conn.execute(
                "INSERT INTO workflow_version (workflow_id, version, code_ref) "
                "VALUES ('test.start_run', '1.0.0', 'test') "
                "ON CONFLICT (workflow_id, version) DO NOTHING"
            )
            # Commit the setup so start_run runs in its own txn
            await conn.commit()

        async with db.conn(user_id=None, role="system") as conn:
            run_id = await runs.start_run(
                conn,
                workflow_id="test.start_run",
                trigger="manual",
                run_input={"building": "3号楼"},
            )
            assert isinstance(run_id, UUID)

            cur = await conn.execute(
                "SELECT status, workflow_id, input FROM workflow_run WHERE id = %s",
                (run_id,),
            )
            row = await cur.fetchone()
            assert row[0] == "pending"
            assert row[1] == "test.start_run"
            assert row[2]["building"] == "3号楼"

    async def test_disabled_workflow_raises(self, db):
        """start_run raises WorkflowDisabledError when workflow is disabled."""
        async with db.conn(user_id=None, role="system") as conn:
            await conn.execute(
                "INSERT INTO workflow (id, enabled, latest_version, display_name, default_trigger) "
                "VALUES ('test.disabled', false, '1.0.0', 'Disabled', 'manual') "
                "ON CONFLICT (id) DO UPDATE SET enabled = false"
            )
            await conn.execute(
                "INSERT INTO workflow_version (workflow_id, version, code_ref) "
                "VALUES ('test.disabled', '1.0.0', 'test') "
                "ON CONFLICT (workflow_id, version) DO NOTHING"
            )
            await conn.commit()

        async with db.conn(user_id=None, role="system") as conn:
            with pytest.raises(WorkflowDisabledError):
                await runs.start_run(
                    conn,
                    workflow_id="test.disabled",
                    trigger="manual",
                )

    async def test_duplicate_correlation_key_raises(self, db):
        """Two runs with the same (workflow_id, correlation_key) conflict."""
        async with db.conn(user_id=None, role="system") as conn:
            await conn.execute(
                "INSERT INTO workflow (id, enabled, latest_version, display_name, default_trigger) "
                "VALUES ('test.dupe', true, '1.0.0', 'Dupe', 'manual') "
                "ON CONFLICT (id) DO UPDATE SET enabled = true"
            )
            await conn.execute(
                "INSERT INTO workflow_version (workflow_id, version, code_ref) "
                "VALUES ('test.dupe', '1.0.0', 'test') "
                "ON CONFLICT (workflow_id, version) DO NOTHING"
            )
            await conn.commit()

        async with db.conn(user_id=None, role="system") as conn:
            await runs.start_run(
                conn,
                workflow_id="test.dupe",
                trigger="manual",
                correlation_key="my-correlation",
            )
            await conn.commit()

        async with db.conn(user_id=None, role="system") as conn:
            with pytest.raises(DuplicateActiveRunError):
                await runs.start_run(
                    conn,
                    workflow_id="test.dupe",
                    trigger="manual",
                    correlation_key="my-correlation",
                )

    async def test_claim_pending_run(self, db):
        """A pending run can be claimed by a worker."""
        async with db.conn(user_id=None, role="system") as conn:
            await conn.execute(
                "INSERT INTO workflow (id, enabled, latest_version, display_name, default_trigger) "
                "VALUES ('test.claim', true, '1.0.0', 'Claim', 'manual') "
                "ON CONFLICT (id) DO UPDATE SET enabled = true"
            )
            await conn.execute(
                "INSERT INTO workflow_version (workflow_id, version, code_ref) "
                "VALUES ('test.claim', '1.0.0', 'test') "
                "ON CONFLICT (workflow_id, version) DO NOTHING"
            )
            await conn.commit()

        async with db.conn(user_id=None, role="system") as conn:
            run_id = await runs.start_run(
                conn, workflow_id="test.claim", trigger="manual"
            )
            await conn.commit()

        async with db.conn(user_id=None, role="system") as conn:
            claimed = await runs.claim_next_run(
                conn, worker="test-worker", ttl_seconds=60
            )
            assert claimed is not None
            assert claimed.id == run_id
            assert claimed.workflow_id == "test.claim"

    async def test_renew_lease_succeeds(self, db):
        """renew_lease extends the lease expiration."""
        async with db.conn(user_id=None, role="system") as conn:
            await conn.execute(
                "INSERT INTO workflow (id, enabled, latest_version, display_name, default_trigger) "
                "VALUES ('test.renew', true, '1.0.0', 'Renew', 'manual') "
                "ON CONFLICT (id) DO UPDATE SET enabled = true"
            )
            await conn.execute(
                "INSERT INTO workflow_version (workflow_id, version, code_ref) "
                "VALUES ('test.renew', '1.0.0', 'test') "
                "ON CONFLICT (workflow_id, version) DO NOTHING"
            )
            await conn.commit()

        async with db.conn(user_id=None, role="system") as conn:
            run_id = await runs.start_run(
                conn, workflow_id="test.renew", trigger="manual"
            )
            await conn.commit()

        async with db.conn(user_id=None, role="system") as conn:
            claimed = await runs.claim_next_run(
                conn, worker="test-worker", ttl_seconds=60
            )
            assert claimed is not None

            ok = await runs.renew_lease(
                conn, run_id=run_id, worker="test-worker", ttl_seconds=120
            )
            assert ok is True

    async def test_renew_lease_fails_when_worker_mismatch(self, db):
        """A different worker cannot renew another worker's lease."""
        async with db.conn(user_id=None, role="system") as conn:
            await conn.execute(
                "INSERT INTO workflow (id, enabled, latest_version, display_name, default_trigger) "
                "VALUES ('test.renew-fail', true, '1.0.0', 'RenewFail', 'manual') "
                "ON CONFLICT (id) DO UPDATE SET enabled = true"
            )
            await conn.execute(
                "INSERT INTO workflow_version (workflow_id, version, code_ref) "
                "VALUES ('test.renew-fail', '1.0.0', 'test') "
                "ON CONFLICT (workflow_id, version) DO NOTHING"
            )
            await conn.commit()

        async with db.conn(user_id=None, role="system") as conn:
            run_id = await runs.start_run(
                conn, workflow_id="test.renew-fail", trigger="manual"
            )
            await conn.commit()

        async with db.conn(user_id=None, role="system") as conn:
            claimed = await runs.claim_next_run(
                conn, worker="worker-A", ttl_seconds=60
            )
            assert claimed is not None

            ok = await runs.renew_lease(
                conn, run_id=run_id, worker="worker-B", ttl_seconds=120
            )
            assert ok is False


# ===================================================================
# Agent-call state machine
# ===================================================================


@NEEDS_DB
class TestAgentCallStateMachine:
    """The agent_call table drives the call_agent state machine."""

    async def test_insert_call_and_get_latest(self, db):
        """Inserting an agent_call and retrieving it works."""
        async with db.conn(user_id=None, role="system") as conn:
            await conn.execute(
                "INSERT INTO workflow (id, enabled, latest_version, display_name, default_trigger) "
                "VALUES ('test.agentcall', true, '1.0.0', 'AgentCall', 'manual') "
                "ON CONFLICT (id) DO UPDATE SET enabled = true"
            )
            await conn.execute(
                "INSERT INTO workflow_version (workflow_id, version, code_ref) "
                "VALUES ('test.agentcall', '1.0.0', 'test') "
                "ON CONFLICT (workflow_id, version) DO NOTHING"
            )
            # Insert a dummy agent row (needed for FK)
            await conn.execute(
                "INSERT INTO agents (id, display_name, tier, skill_list, status) "
                "VALUES (%s, 'Test Agent', 'tier0', '[]'::jsonb, 'active') "
                "ON CONFLICT (id) DO NOTHING",
                (_TEST_AGENT_ID,),
            )
            await conn.commit()

        async with db.conn(user_id=None, role="system") as conn:
            run_id = await runs.start_run(
                conn, workflow_id="test.agentcall", trigger="manual"
            )
            await conn.commit()

        async with db.conn(user_id=None, role="system") as conn:
            row = await agent_calls.insert_call(
                conn,
                run_id=run_id,
                step_key="step-1",
                agent_id=_TEST_AGENT_ID,
                request_hash="abc123",
            )
            assert row.status == "posted"
            assert row.step_key == "step-1"

            retrieved = await agent_calls.get_latest_call(
                conn, run_id=run_id, step_key="step-1"
            )
            assert retrieved is not None
            assert retrieved.correlation_id == row.correlation_id
            assert retrieved.request_hash == "abc123"

    async def test_mark_dispatched(self, db):
        """posted → dispatched transition works."""
        async with db.conn(user_id=None, role="system") as conn:
            await conn.execute(
                "INSERT INTO workflow (id, enabled, latest_version, display_name, default_trigger) "
                "VALUES ('test.dispatch', true, '1.0.0', 'Dispatch', 'manual') "
                "ON CONFLICT (id) DO UPDATE SET enabled = true"
            )
            await conn.execute(
                "INSERT INTO workflow_version (workflow_id, version, code_ref) "
                "VALUES ('test.dispatch', '1.0.0', 'test') "
                "ON CONFLICT (workflow_id, version) DO NOTHING"
            )
            await conn.execute(
                "INSERT INTO agents (id, display_name, tier, skill_list, status) "
                "VALUES (%s, 'Test Agent', 'tier0', '[]'::jsonb, 'active') "
                "ON CONFLICT (id) DO NOTHING",
                (_TEST_AGENT_ID,),
            )
            await conn.commit()

        async with db.conn(user_id=None, role="system") as conn:
            run_id = await runs.start_run(
                conn, workflow_id="test.dispatch", trigger="manual"
            )
            await conn.commit()

        async with db.conn(user_id=None, role="system") as conn:
            call = await agent_calls.insert_call(
                conn,
                run_id=run_id,
                step_key="step-1",
                agent_id=_TEST_AGENT_ID,
                request_hash="abc123",
            )
            ok = await agent_calls.mark_dispatched(
                conn, correlation_id=call.correlation_id
            )
            assert ok is True

            retrieved = await agent_calls.get_latest_call(
                conn, run_id=run_id, step_key="step-1"
            )
            assert retrieved.status == "dispatched"

    async def test_record_response(self, db):
        """posted → dispatched → responded lifecycle."""
        async with db.conn(user_id=None, role="system") as conn:
            await conn.execute(
                "INSERT INTO workflow (id, enabled, latest_version, display_name, default_trigger) "
                "VALUES ('test.respond', true, '1.0.0', 'Respond', 'manual') "
                "ON CONFLICT (id) DO UPDATE SET enabled = true"
            )
            await conn.execute(
                "INSERT INTO workflow_version (workflow_id, version, code_ref) "
                "VALUES ('test.respond', '1.0.0', 'test') "
                "ON CONFLICT (workflow_id, version) DO NOTHING"
            )
            await conn.execute(
                "INSERT INTO agents (id, display_name, tier, skill_list, status) "
                "VALUES (%s, 'Test Agent', 'tier0', '[]'::jsonb, 'active') "
                "ON CONFLICT (id) DO NOTHING",
                (_TEST_AGENT_ID,),
            )
            await conn.commit()

        async with db.conn(user_id=None, role="system") as conn:
            run_id = await runs.start_run(
                conn, workflow_id="test.respond", trigger="manual"
            )
            await conn.commit()

        async with db.conn(user_id=None, role="system") as conn:
            call = await agent_calls.insert_call(
                conn,
                run_id=run_id,
                step_key="step-1",
                agent_id=_TEST_AGENT_ID,
                request_hash="abc123",
            )
            await agent_calls.mark_dispatched(
                conn, correlation_id=call.correlation_id
            )

            # Park the run as waiting_agent so the callback can flip it
            await runs.park_run(
                conn,
                run_id=run_id,
                worker="test-worker",
                status="waiting_agent",
                wake_at=datetime.now(UTC) + timedelta(seconds=60),
            )

            returned_run_id = await agent_calls.record_response(
                conn,
                correlation_id=call.correlation_id,
                agent_id=_TEST_AGENT_ID,
                payload={"status": "ok", "result": {"steps": 4}},
            )
            assert returned_run_id == run_id

            retrieved = await agent_calls.get_latest_call(
                conn, run_id=run_id, step_key="step-1"
            )
            assert retrieved.status == "responded"
            assert retrieved.response["status"] == "ok"
            assert retrieved.response["result"]["steps"] == 4

    async def test_supersede_in_flight(self, db):
        """supersede_in_flight marks posted/dispatched rows as superseded."""
        async with db.conn(user_id=None, role="system") as conn:
            await conn.execute(
                "INSERT INTO workflow (id, enabled, latest_version, display_name, default_trigger) "
                "VALUES ('test.supersede', true, '1.0.0', 'Supersede', 'manual') "
                "ON CONFLICT (id) DO UPDATE SET enabled = true"
            )
            await conn.execute(
                "INSERT INTO workflow_version (workflow_id, version, code_ref) "
                "VALUES ('test.supersede', '1.0.0', 'test') "
                "ON CONFLICT (workflow_id, version) DO NOTHING"
            )
            await conn.execute(
                "INSERT INTO agents (id, display_name, tier, skill_list, status) "
                "VALUES (%s, 'Test Agent', 'tier0', '[]'::jsonb, 'active') "
                "ON CONFLICT (id) DO NOTHING",
                (_TEST_AGENT_ID,),
            )
            await conn.commit()

        async with db.conn(user_id=None, role="system") as conn:
            run_id = await runs.start_run(
                conn, workflow_id="test.supersede", trigger="manual"
            )
            await conn.commit()

        async with db.conn(user_id=None, role="system") as conn:
            await agent_calls.insert_call(
                conn,
                run_id=run_id,
                step_key="step-1",
                agent_id=_TEST_AGENT_ID,
                request_hash="old",
            )
            count = await agent_calls.supersede_in_flight(
                conn, run_id=run_id, step_key="step-1"
            )
            assert count == 1

            retrieved = await agent_calls.get_latest_call(
                conn, run_id=run_id, step_key="step-1"
            )
            assert retrieved.status == "superseded"


# ===================================================================
# Step sequencing — verify the 4 nursing-ops steps chain
# ===================================================================

class TestStepSequencing:
    """Verify the nursing-ops flow step chain without a DB connection."""

    def test_full_chain_order(self):
        """All 4 steps execute in order: schedule → logistics → finance → director."""
        from dl_control.workflows.flows.nursing_ops import nursing_ops_flow

        f = nursing_ops_flow
        key = f.first_key
        visited = []
        while key is not None:
            visited.append(key)
            key = f.next_key(key)
        assert visited == [
            "nursing-schedule-step",
            "logistics-step",
            "finance-step",
            "director-report-step",
        ]
        assert len(visited) == 4

    def test_each_step_produces_agent_task(self):
        """When all 4 agent IDs are given, each prepare returns an AgentTask."""
        from dl_control.workflows.flows.nursing_ops import nursing_ops_flow

        f = nursing_ops_flow
        inputs = {
            "nursing_agent_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01",
            "logistics_agent_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa02",
            "general_agent_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa03",
            "director_agent_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa04",
            "building": "3号楼",
        }
        outputs: dict[str, str] = {}

        for i, step in enumerate(f.steps):
            task = step.call_agent.prepare(inputs, outputs)
            assert isinstance(task, AgentTask), f"{step.key}: expected AgentTask"
            assert task.message, f"{step.key}: message is empty"

            # Simulate output from this step for the next one
            outputs[step.key] = f'{{"step": "{step.key}", "completed": true}}'

    def test_step_outputs_passed_between_steps(self):
        """Output from step N is passed to step N+1 via the outputs dict."""
        from dl_control.workflows.flows.nursing_ops import nursing_ops_flow

        f = nursing_ops_flow
        inputs = {
            "nursing_agent_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01",
            "logistics_agent_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa02",
            "general_agent_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa03",
            "director_agent_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa04",
        }
        outputs: dict[str, str] = {}

        # Step 1
        s1 = f.steps[0]
        t1 = s1.call_agent.prepare(inputs, outputs)
        assert "排班" in t1.message
        outputs[s1.key] = '{"building": "3", "staff_count": 10}'

        # Step 2 receives step 1 output
        s2 = f.steps[1]
        t2 = s2.call_agent.prepare(inputs, outputs)
        assert "staff_count" in t2.message  # from step 1
        outputs[s2.key] = '{"items": 42, "alerts": 3}'

        # Step 3 receives both step 1 + 2 outputs
        s3 = f.steps[2]
        t3 = s3.call_agent.prepare(inputs, outputs)
        assert "staff_count" in t3.message  # from step 1
        assert "items" in t3.message  # from step 2
        outputs[s3.key] = '{"total_cost": 50000}'

        # Step 4 receives all 3 prior outputs
        s4 = f.steps[3]
        t4 = s4.call_agent.prepare(inputs, outputs)
        assert "staff_count" in t4.message  # from step 1
        assert "items" in t4.message  # from step 2
        assert "total_cost" in t4.message  # from step 3
