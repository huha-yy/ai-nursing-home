"""Nursing operations multi-agent workflow.

护理科 generates schedule → 总务科 plans deliveries → 财务科(通用助手) estimates costs
→ 院长 generates weekly ops report.

Each step is a CallAgent dispatch: the workflow sends a task message to the
OpenClaw agent container, which runs the corresponding skill. Steps are
sequential — each step's output feeds into the next.

Workflow input:
  - nursing_agent_id (str, optional): UUID of the 护理科 agent.
  - logistics_agent_id (str, optional): UUID of the 总务科 agent.
  - general_agent_id (str, optional): UUID of the 通用助手 agent (acts as 财务科).
  - director_agent_id (str, optional): UUID of the 院长 agent.
  - building (str, optional): Target building (default "3号楼").
  - week_start (str, optional): ISO date for the Monday of the target week.
"""

from __future__ import annotations

from typing import Any

from dl_control.workflows import config_cache
from dl_control.workflows.model import (
    AgentTask,
    CallAgent,
    Flow,
    Retry,
    Step,
    StepContext,
    StepResult,
)

# ---------------------------------------------------------------------------
# Skill invocation prefix for unattended multi-agent pipeline steps.
# Each agent is given a specific department task with the data from prior
# steps. Agents MUST NOT ask questions — they execute autonomously.
# ---------------------------------------------------------------------------

_OPS_PREFIX = (
    "⚡ 无人值守多智能体协作任务。你的部门被分配了一个具体任务。\n"
    "铁律：禁止询问用户、禁止等待确认、禁止输出\"是否需要\"等提问。\n"
    "直接执行，完成后输出结果。遇到错误自动修复一次，失败则返回错误信息。\n"
    "你的输出将作为下一个部门智能体的输入。\n\n"
)


def _agent_task(
    agent_id_key: str,
    message_builder,
    workflow_id: str | None = None,
):
    """Build a deterministic prepare function for CallAgent.

    agent_id_key: key in the workflow input dict holding the agent UUID.
    message_builder: fn(input, outputs) -> str message for the agent.
    workflow_id: used to look up the DB-backed default from config_cache.
    """

    def prepare(input: dict[str, Any], outputs: dict[str, Any]):
        raw = input.get(agent_id_key)
        if not raw:
            db_default = config_cache.get_default(workflow_id) if workflow_id else None
            raw = db_default or config_cache.get_hardcoded_fallback()
        if not raw:
            raise KeyError(agent_id_key)
        agent_id = raw if hasattr(raw, "hex") else __import__("uuid").UUID(raw)
        message = message_builder(input, outputs)
        return AgentTask(agent_id=agent_id, message=message)

    return prepare


# --- Step prepare functions ---


def _prepare_nursing_schedule(input: dict[str, Any], outputs: dict[str, Any]) -> AgentTask:
    raw = input.get("nursing_agent_id")
    if not raw:
        raw = config_cache.get_default("nursing.ops") or config_cache.get_hardcoded_fallback()
    if not raw:
        raise KeyError("nursing_agent_id")
    from uuid import UUID

    agent_id = UUID(raw) if isinstance(raw, str) else raw
    building = input.get("building", "3号楼")
    week_start = input.get("week_start", "")
    msg = (
        _OPS_PREFIX
        + f"任务：生成本周{building}护工排班表。\n"
        + "使用 nursing-schedule 技能生成完整的周排班。\n"
        + "操作步骤：\n"
        + "1. 读取 /opt/openclaw/skills/custom/nursing-schedule/SKILL.md\n"
        + "2. 使用 process 工具调用 handler.generate_weekly_schedule\n"
        + "3. 输出排班结果 — staff_count, total_shifts, day_shifts, night_shifts\n"
        + "输出格式：JSON 对象，包含 week, building, staff_count, total_shifts, "
        + "day_shifts, night_shifts, schedules 数组。"
    )
    if week_start:
        msg += f"\n周起始日期：{week_start}"
    return AgentTask(agent_id=agent_id, message=msg)


def _prepare_logistics(input: dict[str, Any], outputs: dict[str, Any]) -> AgentTask:
    raw = input.get("logistics_agent_id")
    if not raw:
        raw = config_cache.get_default("nursing.ops") or config_cache.get_hardcoded_fallback()
    if not raw:
        raise KeyError("logistics_agent_id")
    from uuid import UUID

    agent_id = UUID(raw) if isinstance(raw, str) else raw
    building = input.get("building", "3号楼")
    schedule_result = outputs.get("nursing-schedule-step", "{}")
    msg = (
        _OPS_PREFIX
        + f"任务：根据{building}护工排班结果安排物资配送计划。\n"
        + "使用 logistics-inventory 技能检查库存，并制定配送计划。\n"
        + "操作步骤：\n"
        + "1. 读取 /opt/openclaw/skills/custom/logistics-inventory/SKILL.md\n"
        + "2. 检查库存水平（重点关注低于安全库存的物资）\n"
        + "3. 根据排班数据制定配送计划（消耗品、餐食、医疗用品）\n"
        + "4. 输出物资计划和库存预警\n\n"
        + f"排班数据：{schedule_result}"
    )
    return AgentTask(agent_id=agent_id, message=msg)


def _prepare_finance(input: dict[str, Any], outputs: dict[str, Any]) -> AgentTask:
    raw = input.get("general_agent_id")
    if not raw:
        raw = config_cache.get_default("nursing.ops") or config_cache.get_hardcoded_fallback()
    if not raw:
        raise KeyError("general_agent_id")
    from uuid import UUID

    agent_id = UUID(raw) if isinstance(raw, str) else raw
    schedule = outputs.get("nursing-schedule-step", "")
    logistics = outputs.get("logistics-step", "")
    msg = (
        _OPS_PREFIX
        + "任务：你当前以财务科身份运行。根据排班和物资计划生成运营成本预估。\n"
        + "使用 finance-query 技能查询财务数据。\n"
        + "操作步骤：\n"
        + "1. 读取 /opt/openclaw/skills/custom/finance-query/SKILL.md\n"
        + "2. 分析人力成本（根据排班数据估算）\n"
        + "3. 分析物资成本（根据配送计划估算）\n"
        + "4. 输出运营成本预估报告\n\n"
        + f"排班数据：{schedule}\n"
        + f"物资数据：{logistics}"
    )
    return AgentTask(agent_id=agent_id, message=msg)


def _prepare_director_report(input: dict[str, Any], outputs: dict[str, Any]) -> AgentTask:
    raw = input.get("director_agent_id")
    if not raw:
        raw = config_cache.get_default("nursing.ops") or config_cache.get_hardcoded_fallback()
    if not raw:
        raise KeyError("director_agent_id")
    from uuid import UUID

    agent_id = UUID(raw) if isinstance(raw, str) else raw
    schedule = outputs.get("nursing-schedule-step", "")
    logistics = outputs.get("logistics-step", "")
    finance = outputs.get("finance-step", "")
    msg = (
        _OPS_PREFIX
        + "任务：作为院长，汇总所有部门的输出，生成本周运营综合报表。\n"
        + "使用 report-generate 技能生成报表。\n"
        + "操作步骤：\n"
        + "1. 读取 /opt/openclaw/skills/custom/report-generate/SKILL.md\n"
        + "2. 综合排班、物资、成本数据生成周报表\n"
        + "3. 报表需包含：排班概况、物资配送、成本预估、重点关注事项\n\n"
        + f"排班数据：{schedule}\n"
        + f"物资数据：{logistics}\n"
        + f"成本数据：{finance}"
    )
    return AgentTask(agent_id=agent_id, message=msg)


# --- Flow definition ---

nursing_ops_flow = Flow(
    id="nursing.ops",
    version="1.0.0",
    steps=[
        Step(
            "nursing-schedule-step",
            call_agent=CallAgent(prepare=_prepare_nursing_schedule, timeout_seconds=600),
            retry=Retry(max_attempts=2, base_seconds=15),
        ),
        Step(
            "logistics-step",
            call_agent=CallAgent(prepare=_prepare_logistics, timeout_seconds=600),
            retry=Retry(max_attempts=2, base_seconds=15),
        ),
        Step(
            "finance-step",
            call_agent=CallAgent(prepare=_prepare_finance, timeout_seconds=600),
            retry=Retry(max_attempts=2, base_seconds=15),
        ),
        Step(
            "director-report-step",
            call_agent=CallAgent(prepare=_prepare_director_report, timeout_seconds=600),
            retry=Retry(max_attempts=2, base_seconds=15),
        ),
    ],
)
