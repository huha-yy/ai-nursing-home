"""Unit tests for the nursing-ops workflow — prepare functions, Flow model,
config_cache, and agent resolution.

These tests are pure Python with no Docker/Postgres dependencies. They verify:
  1. Each prepare function returns a valid AgentTask with correct message content.
  2. Agent resolution priority (explicit input > precreated cache > workflow default).
  3. Flow model construction (4 steps, correct keys, retry config).
  4. config_cache in-memory operations (get/set/fallback).

Run:
    uv run pytest tests/test_workflow_unit.py -v
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from dl_control.workflows import config_cache
from dl_control.workflows.flows.nursing_ops import (
    _OPS_PREFIX,
    _prepare_director_report,
    _prepare_finance,
    _prepare_logistics,
    _prepare_nursing_schedule,
    _resolve_agent,
    nursing_ops_flow,
)
from dl_control.workflows.model import (
    AgentTask,
    CallAgent,
    Flow,
    Retry,
    Step,
)

# ---------------------------------------------------------------------------
# Test UUIDs — stable across test runs, never change
# ---------------------------------------------------------------------------
_NURSING_UUID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_LOGISTICS_UUID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
_GENERAL_UUID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
_DIRECTOR_UUID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")


# ===================================================================
# 1. _resolve_agent — agent resolution priority
# ===================================================================


class TestResolveAgent:
    """Agent resolution tests: the four-tier priority chain."""

    def test_explicit_uuid_in_input_wins(self):
        """When input contains an explicit UUID, use it directly."""
        result = _resolve_agent(
            {"nursing_agent_id": str(_NURSING_UUID)},
            "nursing_agent_id",
            "nursing-dept",
        )
        assert result == _NURSING_UUID

    def test_uuid_object_not_string(self):
        """Input can be a UUID object, not just a string."""
        result = _resolve_agent(
            {"nursing_agent_id": _NURSING_UUID},
            "nursing_agent_id",
            "nursing-dept",
        )
        assert result == _NURSING_UUID

    def test_precreated_cache_fallback(self):
        """When the input has no agent ID, resolve from the precreated cache."""
        config_cache.set_precreated("nursing-dept", _NURSING_UUID)
        try:
            result = _resolve_agent({}, "nursing_agent_id", "nursing-dept")
            assert result == _NURSING_UUID
        finally:
            config_cache.set_precreated("nursing-dept", None)  # clean up

    def test_workflow_default_fallback(self):
        """Last DB fallback: get_default('nursing.ops')."""
        config_cache.set_default("nursing.ops", _NURSING_UUID)
        try:
            result = _resolve_agent({}, "nursing_agent_id", "nursing-dept")
            assert result == _NURSING_UUID
        finally:
            config_cache.set_default("nursing.ops", None)

    def test_no_agent_raises_keyerror(self):
        """When all fallbacks are exhausted, raise a friendly KeyError."""
        with pytest.raises(KeyError) as exc:
            _resolve_agent({}, "nursing_agent_id", "nursing-dept")
        msg = str(exc.value)
        assert "nursing_agent_id" in msg
        assert "nursing-dept" in msg
        assert "请先在管理后台为护理运营流程配置智能体" in msg

    def test_precreated_beats_workflow_default(self):
        """precreated cache takes priority over the per-workflow default."""
        config_cache.set_precreated("nursing-dept", _NURSING_UUID)
        config_cache.set_default("nursing.ops", _DIRECTOR_UUID)
        try:
            result = _resolve_agent({}, "nursing_agent_id", "nursing-dept")
            assert result == _NURSING_UUID  # precreated wins, not director
        finally:
            config_cache.set_precreated("nursing-dept", None)
            config_cache.set_default("nursing.ops", None)

    def test_missing_precreated_skips_to_workflow_default(self):
        """When precreated cache misses, fall through to workflow default."""
        config_cache.set_default("nursing.ops", _DIRECTOR_UUID)
        try:
            result = _resolve_agent({}, "nursing_agent_id", "nursing-dept")
            assert result == _DIRECTOR_UUID
        finally:
            config_cache.set_default("nursing.ops", None)


# ===================================================================
# 2. Prepare functions — AgentTask construction
# ===================================================================


def _setup_agent_cache():
    """Install all 4 nursing agent UUIDs in the precreated cache."""
    config_cache.set_precreated("nursing-dept", _NURSING_UUID)
    config_cache.set_precreated("logistics-dept", _LOGISTICS_UUID)
    config_cache.set_precreated("general-assistant", _GENERAL_UUID)
    config_cache.set_precreated("director", _DIRECTOR_UUID)


def _teardown_agent_cache():
    """Remove all test UUIDs from the precreated cache."""
    for pid in ("nursing-dept", "logistics-dept", "general-assistant", "director"):
        config_cache.set_precreated(pid, None)


@pytest.fixture(autouse=True)
def _clean_cache():
    """Each test starts and ends with a clean precreated cache, so
    resolution tests don't leak state between one another."""
    _teardown_agent_cache()
    yield
    _teardown_agent_cache()


class TestPrepareNursingSchedule:
    """Step 1: 护理科 uses nursing-schedule to generate weekly roster."""

    def test_returns_agent_task(self):
        task = _prepare_nursing_schedule(
            {"nursing_agent_id": str(_NURSING_UUID)}, {}
        )
        assert isinstance(task, AgentTask), f"Expected AgentTask, got {type(task)}"
        assert task.agent_id == _NURSING_UUID

    def test_message_contains_skill_name(self):
        task = _prepare_nursing_schedule(
            {"nursing_agent_id": str(_NURSING_UUID)}, {}
        )
        assert "nursing-schedule" in task.message
        assert "generate_weekly_schedule" in task.message

    def test_message_has_ops_prefix(self):
        task = _prepare_nursing_schedule(
            {"nursing_agent_id": str(_NURSING_UUID)}, {}
        )
        assert _OPS_PREFIX in task.message

    def test_default_building_is_3hao(self):
        task = _prepare_nursing_schedule(
            {"nursing_agent_id": str(_NURSING_UUID)}, {}
        )
        assert "3号楼" in task.message

    def test_custom_building(self):
        task = _prepare_nursing_schedule(
            {"nursing_agent_id": str(_NURSING_UUID), "building": "5号楼"}, {}
        )
        assert "5号楼" in task.message
        assert "3号楼" not in task.message

    def test_week_start_appended_when_present(self):
        task = _prepare_nursing_schedule(
            {"nursing_agent_id": str(_NURSING_UUID), "week_start": "2026-07-27"},
            {},
        )
        assert "2026-07-27" in task.message

    def test_week_start_absent_when_not_provided(self):
        task = _prepare_nursing_schedule(
            {"nursing_agent_id": str(_NURSING_UUID)}, {}
        )
        # week_start defaults to "" — only appended when non-empty
        assert "周起始日期：" not in task.message

    def test_resolves_from_precreated_cache(self):
        config_cache.set_precreated("nursing-dept", _NURSING_UUID)
        task = _prepare_nursing_schedule({}, {})
        assert task.agent_id == _NURSING_UUID

    def test_raises_when_no_agent(self):
        with pytest.raises(KeyError):
            _prepare_nursing_schedule({}, {})


class TestPrepareLogistics:
    """Step 2: 总务科 checks inventory and plans deliveries."""

    def test_returns_agent_task(self):
        task = _prepare_logistics(
            {"logistics_agent_id": str(_LOGISTICS_UUID)}, {}
        )
        assert isinstance(task, AgentTask)
        assert task.agent_id == _LOGISTICS_UUID

    def test_message_contains_skill(self):
        task = _prepare_logistics(
            {"logistics_agent_id": str(_LOGISTICS_UUID)}, {}
        )
        assert "logistics-inventory" in task.message

    def test_receives_schedule_data(self):
        schedule_data = '{"staff_count": 10, "total_shifts": 14}'
        task = _prepare_logistics(
            {"logistics_agent_id": str(_LOGISTICS_UUID)},
            {"nursing-schedule-step": schedule_data},
        )
        assert "staff_count" in task.message
        assert "total_shifts" in task.message

    def test_resolves_from_precreated_cache(self):
        config_cache.set_precreated("logistics-dept", _LOGISTICS_UUID)
        task = _prepare_logistics({}, {})
        assert task.agent_id == _LOGISTICS_UUID

    def test_raises_when_no_agent(self):
        with pytest.raises(KeyError):
            _prepare_logistics({}, {})


class TestPrepareFinance:
    """Step 3: 通用助手 acts as 财务科 — cost estimation."""

    def test_returns_agent_task(self):
        task = _prepare_finance(
            {"general_agent_id": str(_GENERAL_UUID)}, {}
        )
        assert isinstance(task, AgentTask)
        assert task.agent_id == _GENERAL_UUID

    def test_message_contains_skill(self):
        task = _prepare_finance(
            {"general_agent_id": str(_GENERAL_UUID)}, {}
        )
        assert "finance-query" in task.message

    def test_receives_schedule_and_logistics_data(self):
        task = _prepare_finance(
            {"general_agent_id": str(_GENERAL_UUID)},
            {
                "nursing-schedule-step": '{"staff": 5}',
                "logistics-step": '{"items": 42}',
            },
        )
        assert "staff" in task.message
        assert "items" in task.message

    def test_resolves_from_precreated_cache(self):
        config_cache.set_precreated("general-assistant", _GENERAL_UUID)
        task = _prepare_finance({}, {})
        assert task.agent_id == _GENERAL_UUID

    def test_raises_when_no_agent(self):
        with pytest.raises(KeyError):
            _prepare_finance({}, {})


class TestPrepareDirectorReport:
    """Step 4: 院长 generates the weekly ops report."""

    def test_returns_agent_task(self):
        task = _prepare_director_report(
            {"director_agent_id": str(_DIRECTOR_UUID)}, {}
        )
        assert isinstance(task, AgentTask)
        assert task.agent_id == _DIRECTOR_UUID

    def test_message_contains_skill(self):
        task = _prepare_director_report(
            {"director_agent_id": str(_DIRECTOR_UUID)}, {}
        )
        assert "report-generate" in task.message

    def test_receives_all_prior_step_data(self):
        task = _prepare_director_report(
            {"director_agent_id": str(_DIRECTOR_UUID)},
            {
                "nursing-schedule-step": '{"schedule": "data"}',
                "logistics-step": '{"logistics": "data"}',
                "finance-step": '{"finance": "data"}',
            },
        )
        assert '"schedule": "data"' in task.message
        assert '"logistics": "data"' in task.message
        assert '"finance": "data"' in task.message

    def test_resolves_from_precreated_cache(self):
        config_cache.set_precreated("director", _DIRECTOR_UUID)
        task = _prepare_director_report({}, {})
        assert task.agent_id == _DIRECTOR_UUID

    def test_raises_when_no_agent(self):
        with pytest.raises(KeyError):
            _prepare_director_report({}, {})


# ===================================================================
# 3. Flow model — construction and navigation
# ===================================================================


class TestFlowModel:
    """The nursing_ops_flow Flow object."""

    def test_flow_id_and_version(self):
        assert nursing_ops_flow.id == "nursing.ops"
        assert nursing_ops_flow.version == "1.0.0"

    def test_exactly_four_steps(self):
        assert len(nursing_ops_flow.steps) == 4

    def test_step_keys_in_order(self):
        keys = [s.key for s in nursing_ops_flow.steps]
        assert keys == [
            "nursing-schedule-step",
            "logistics-step",
            "finance-step",
            "director-report-step",
        ]

    def test_first_key(self):
        assert nursing_ops_flow.first_key == "nursing-schedule-step"

    def test_next_key_chain(self):
        f = nursing_ops_flow
        assert f.next_key("nursing-schedule-step") == "logistics-step"
        assert f.next_key("logistics-step") == "finance-step"
        assert f.next_key("finance-step") == "director-report-step"
        assert f.next_key("director-report-step") is None  # end of chain

    def test_each_step_is_call_agent(self):
        for s in nursing_ops_flow.steps:
            assert s.call_agent is not None, f"{s.key}: missing call_agent"
            assert s.handler is None, f"{s.key}: should not have handler"
            assert s.timer is None, f"{s.key}: should not have timer"

    def test_each_step_has_retry(self):
        for s in nursing_ops_flow.steps:
            assert s.retry is not None, f"{s.key}: missing retry"
            assert s.retry.max_attempts == 2
            assert s.retry.base_seconds == 15

    def test_each_step_has_timeout(self):
        for s in nursing_ops_flow.steps:
            assert s.call_agent.timeout_seconds == 600

    def test_call_agent_prepare_is_callable(self):
        for s in nursing_ops_flow.steps:
            assert callable(s.call_agent.prepare), f"{s.key}: prepare not callable"

    def test_by_key_lookup(self):
        for s in nursing_ops_flow.steps:
            assert nursing_ops_flow.by_key[s.key] is s

    def test_duplicate_keys_rejected(self):
        with pytest.raises(ValueError, match="duplicate"):
            Flow("test.dupe", "1.0.0", steps=[
                Step("a", call_agent=CallAgent(prepare=lambda i, o: AgentTask(
                    agent_id=_NURSING_UUID, message=""), timeout_seconds=60)),
                Step("a", call_agent=CallAgent(prepare=lambda i, o: AgentTask(
                    agent_id=_NURSING_UUID, message=""), timeout_seconds=60)),
            ])

    def test_empty_steps_rejected(self):
        with pytest.raises(ValueError, match="at least one step"):
            Flow("test.empty", "1.0.0", steps=[])

    def test_done_keyword_rejected(self):
        with pytest.raises(ValueError, match="reserved"):
            Flow("test.done", "1.0.0", steps=[
                Step("__done__", call_agent=CallAgent(prepare=lambda i, o: AgentTask(
                    agent_id=_NURSING_UUID, message=""), timeout_seconds=60)),
            ])


# ===================================================================
# 4. Retry policy
# ===================================================================


class TestRetry:
    """The Retry dataclass computes backoff delays correctly."""

    def test_defaults(self):
        r = Retry()
        assert r.max_attempts == 3
        assert r.base_seconds == 30.0
        assert r.cap_seconds == 3600.0

    def test_workflow_retry_config(self):
        """All nursing flow steps use max_attempts=2, base_seconds=15."""
        r = Retry(max_attempts=2, base_seconds=15)
        assert r.delay_after(1) == 15.0  # base * 2^0
        assert r.delay_after(2) == 30.0  # base * 2^1

    def test_backoff_clamped_to_cap(self):
        r = Retry(max_attempts=5, base_seconds=100.0, cap_seconds=200.0)
        assert r.delay_after(1) == 100.0
        assert r.delay_after(2) == 200.0  # capped
        assert r.delay_after(3) == 200.0  # capped

    def test_negative_base_raises(self):
        with pytest.raises(ValueError, match="base_seconds"):
            Retry(max_attempts=1, base_seconds=-1)

    def test_zero_max_attempts_raises(self):
        with pytest.raises(ValueError, match="max_attempts"):
            Retry(max_attempts=0)

    def test_cap_below_base_raises(self):
        with pytest.raises(ValueError, match="cap_seconds"):
            Retry(max_attempts=2, base_seconds=60.0, cap_seconds=30.0)


# ===================================================================
# 5. config_cache — in-memory defaults
# ===================================================================


class TestConfigCache:
    """Synchronous cache for workflow-level agent defaults."""

    def test_get_default_when_empty_returns_none(self):
        assert config_cache.get_default("nursing.ops") is None

    def test_set_and_get_default(self):
        config_cache.set_default("nursing.ops", _NURSING_UUID)
        try:
            assert config_cache.get_default("nursing.ops") == _NURSING_UUID
        finally:
            config_cache.set_default("nursing.ops", None)

    def test_set_none_removes_default(self):
        config_cache.set_default("nursing.ops", _NURSING_UUID)
        config_cache.set_default("nursing.ops", None)
        assert config_cache.get_default("nursing.ops") is None

    def test_get_agent_by_precreated(self):
        config_cache.set_precreated("nursing-dept", _NURSING_UUID)
        try:
            assert config_cache.get_agent_by_precreated("nursing-dept") == _NURSING_UUID
        finally:
            config_cache.set_precreated("nursing-dept", None)

    def test_get_agent_by_precreated_miss_returns_none(self):
        assert config_cache.get_agent_by_precreated("nonexistent") is None

    def test_get_hardcoded_fallback_always_none(self):
        # The legacy UUID is dead — prepare functions must use
        # get_agent_by_precreated() or get_default() instead.
        assert config_cache.get_hardcoded_fallback() is None


# ===================================================================
# 6. Regression — each step maps to the correct precreated_id
# ===================================================================


class TestAgentStepMapping:
    """Each workflow step dispatches to a specific nursing department agent."""

    STEP_KEY_TO_PRECREATED = {
        "nursing-schedule-step": "nursing-dept",
        "logistics-step": "logistics-dept",
        "finance-step": "general-assistant",
        "director-report-step": "director",
    }

    @pytest.mark.parametrize("step_key,expected_pid", list(STEP_KEY_TO_PRECREATED.items()))
    def test_step_maps_to_correct_precreated_id(self, step_key, expected_pid):
        """Correspondence table is correct — the prepare function for each
        step uses the right precreated_id. This is verified by inspecting
        the prepare closure rather than parsing AST."""
        step = nursing_ops_flow.by_key[step_key]
        # Set the corresponding precreated cache entry to a known UUID
        known = uuid4()
        config_cache.set_precreated(expected_pid, known)
        try:
            # When no explicit agent_id is given, the prepare function
            # falls back to get_agent_by_precreated(expected_pid).
            task = step.call_agent.prepare({}, {})
            assert task.agent_id == known, (
                f"{step_key}: expected {expected_pid} ({known}), got {task.agent_id}"
            )
        finally:
            config_cache.set_precreated(expected_pid, None)
