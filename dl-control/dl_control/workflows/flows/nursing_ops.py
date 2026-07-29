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


def _resolve_agent(input: dict[str, Any], key: str, precreated_id: str):
    """Resolve an agent UUID for a workflow step.

    Priority order:
    1. Explicit value in workflow input (``input[key]``).
    2. DB-backed precreated-agent cache (``get_agent_by_precreated``).
    3. Per-workflow default_agent_id (``get_default("nursing.ops")``).
    4. Raise ``KeyError`` with a helpful message.
    """
    from uuid import UUID

    raw = input.get(key)
    if not raw:
        raw = config_cache.get_agent_by_precreated(precreated_id)
    if not raw:
        raw = config_cache.get_default("nursing.ops")
    if not raw:
        raise KeyError(
            f"{key}: no explicit agent ID provided and "
            f"'{precreated_id}' agent not found in the precreated-agent cache. "
            f"请先在管理后台为护理运营流程配置智能体。"
        )
    return UUID(raw) if isinstance(raw, str) else raw


# --- Step prepare functions ---


def _prepare_nursing_schedule(input: dict[str, Any], outputs: dict[str, Any]) -> AgentTask:
    agent_id = _resolve_agent(input, "nursing_agent_id", "nursing-dept")
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
    agent_id = _resolve_agent(input, "logistics_agent_id", "logistics-dept")
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
    agent_id = _resolve_agent(input, "general_agent_id", "general-assistant")
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
    agent_id = _resolve_agent(input, "director_agent_id", "director")
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
