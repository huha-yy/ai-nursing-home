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

i18n（2026-10-08 英文演示收口）：prepare 时读 demo_lang marker 切双语 prompt
——en 下任务指令/输出要求全英文，LLM 产英文周报。口径红线：排班步骤的
JSON 键（week/building/白班/夜班…）保持中文枚举原串（reports 页 JS 与
上游解析按中文键取值），只有 prose（Markdown 报告正文）英文化。
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

_OPS_PREFIX = {
    "zh": (
        "⚡ 无人值守多智能体协作任务。你的部门被分配了一个具体任务。\n"
        "铁律：禁止询问用户、禁止等待确认、禁止输出\"是否需要\"等提问。\n"
        "直接执行，完成后输出结果。遇到错误自动修复一次，失败则返回错误信息。\n"
        "报告正文中禁止出现 runId/会话 ID 等内部标识。\n"
        "你的输出将作为下一个部门智能体的输入。\n\n"
    ),
    "en": (
        "⚡ Unattended multi-agent collaboration task. Your department has been "
        "assigned a specific task.\n"
        "Iron rules: never ask the user questions, never wait for confirmation, "
        "never output phrases like \"Do you need\".\n"
        "Execute directly and output the result when done. On errors, "
        "self-recover once; if that fails, return the error message.\n"
        "Never mention internal identifiers such as runId or session IDs in "
        "the report body.\n"
        "Your output will be the input of the next department's agent.\n"
        "If source data contains Chinese department/role names, render them in "
        "English (院长→Director, 护理科→Nursing Dept, 总务科→Logistics Dept, "
        "财务科→Finance Dept, 餐饮→Catering, 做六休一→work 6 days then rest 1).\n\n"
    ),
}


def _lang() -> str:
    """演示语言：demo_lang marker（en）→ 英文 prompt；缺省中文。

    prepare 每次调度时读（不是 import 时）——切语言后无需重启即生效。
    """
    try:
        from dl_control import i18n

        return i18n.demo_lang() or "zh"
    except Exception:
        return "zh"


def _ctx(envelope: Any) -> str:
    """下游步骤的输入上下文：只取 agent 最终回答文本。

    workflow_step.output 存的是完整回执信封（runId / result.meta 用量
    统计 / 过程 payloads）——整包塞进下一步 prompt 会把内部 runId 带进
    LLM 产出的报告正文（2026-10-08 演示踩坑），且 meta 块白白吃数千
    token。取 payloads 最后一条非空文本（agent 的最终答案）。
    """
    try:
        texts = [p["text"] for p in envelope["result"]["payloads"] if p.get("text")]
        return texts[-1] if texts else str(envelope)
    except Exception:
        return str(envelope)


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
    if _lang() == "en":
        msg = (
            _OPS_PREFIX["en"]
            + f"Task: generate this week's caregiver shift schedule for {building}.\n"
            + "Use the nursing-schedule skill to produce the full weekly schedule.\n"
            + "Steps:\n"
            + "1. Read /opt/openclaw/skills/custom/nursing-schedule/SKILL.md\n"
            + "2. Use the process tool to call handler.generate_weekly_schedule\n"
            + "3. Output the schedule result — staff_count, total_shifts, "
            + "day_shifts, night_shifts, schedule\n"
            + "Output format: a JSON object with keys week, building, staff_count, "
            + "total_shifts, day_shifts, night_shifts, schedule. schedule is the "
            + "per-day detail array (each day {\"date\",\"白班\",\"夜班\"}, names "
            + "joined with 、) — copy the skill's return value VERBATIM; do not "
            + "rewrite, abbreviate, or drop names/dates. NOTE: keep the JSON keys "
            + "exactly as given (白班/夜班), only report prose should be English."
        )
    else:
        msg = (
            _OPS_PREFIX["zh"]
            + f"任务：生成本周{building}护工排班表。\n"
            + "使用 nursing-schedule 技能生成完整的周排班。\n"
            + "操作步骤：\n"
            + "1. 读取 /opt/openclaw/skills/custom/nursing-schedule/SKILL.md\n"
            + "2. 使用 process 工具调用 handler.generate_weekly_schedule\n"
            + "3. 输出排班结果 — staff_count, total_shifts, day_shifts, night_shifts, schedule\n"
            + "输出格式：JSON 对象，键为 week, building, staff_count, total_shifts, "
            + "day_shifts, night_shifts, schedule。schedule 是逐日明细数组（每天 "
            + '{"date","白班","夜班"}，人名顿号分隔），必须逐字复制技能返回值，'
            + "不得改写、缩写或省略人名/日期。"
        )
    if week_start:
        msg += f"\n周起始日期：{week_start}" if _lang() != "en" else f"\nWeek start: {week_start}"
    return AgentTask(agent_id=agent_id, message=msg)


def _prepare_logistics(input: dict[str, Any], outputs: dict[str, Any]) -> AgentTask:
    agent_id = _resolve_agent(input, "logistics_agent_id", "logistics-dept")
    building = input.get("building", "3号楼")
    schedule_result = _ctx(outputs.get("nursing-schedule-step", "{}"))
    if _lang() == "en":
        msg = (
            _OPS_PREFIX["en"]
            + f"Task: plan supplies delivery for {building} based on the caregiver "
              "shift schedule.\n"
            + "Use the logistics-inventory skill to check inventory and draft the "
              "delivery plan.\n"
            + "Steps:\n"
            + "1. Read /opt/openclaw/skills/custom/logistics-inventory/SKILL.md\n"
            + "2. Check inventory levels (flag items below safety stock)\n"
            + "3. Draft the delivery plan from the schedule data (consumables, "
              "meals, medical supplies)\n"
            + "4. Output the supplies plan and inventory alerts\n"
            + "Output format: a Markdown delivery-plan report (title + sections + "
              "tables). Do NOT output JSON code blocks — the upstream schedule "
              "data is JSON and is input only; present the report as Markdown "
              "tables (inventory alert table / per-day delivery table / purchase "
              "suggestions). Write the report in English.\n\n"
            + f"Schedule data: {schedule_result}"
        )
    else:
        msg = (
            _OPS_PREFIX["zh"]
            + f"任务：根据{building}护工排班结果安排物资配送计划。\n"
            + "使用 logistics-inventory 技能检查库存，并制定配送计划。\n"
            + "操作步骤：\n"
            + "1. 读取 /opt/openclaw/skills/custom/logistics-inventory/SKILL.md\n"
            + "2. 检查库存水平（重点关注低于安全库存的物资）\n"
            + "3. 根据排班数据制定配送计划（消耗品、餐食、医疗用品）\n"
            + "4. 输出物资计划和库存预警\n"
            + "输出格式：Markdown 配送计划报告（标题+分节+表格），禁止输出 JSON 代码块——"
            + "上游排班数据是 JSON，只作输入用，报告一律用 Markdown 表格呈现"
            + "（库存预警表 / 逐日配送量表 / 采购建议清单）。\n\n"
            + f"排班数据：{schedule_result}"
        )
    return AgentTask(agent_id=agent_id, message=msg)


def _prepare_finance(input: dict[str, Any], outputs: dict[str, Any]) -> AgentTask:
    agent_id = _resolve_agent(input, "general_agent_id", "general-assistant")
    schedule = _ctx(outputs.get("nursing-schedule-step", ""))
    logistics = _ctx(outputs.get("logistics-step", ""))
    if _lang() == "en":
        msg = (
            _OPS_PREFIX["en"]
            + "Task: you are operating as the finance department. Estimate "
              "operating costs from the schedule and supplies plan.\n"
            + "Use the finance-query skill to query financial data.\n"
            + "Steps:\n"
            + "1. Read /opt/openclaw/skills/custom/finance-query/SKILL.md\n"
            + "2. Analyze labor costs (estimated from the schedule data)\n"
            + "3. Analyze supplies costs (estimated from the delivery plan)\n"
            + "4. Output the operating cost estimate report\n"
            + "Output format: a Markdown report (title + sections + tables). "
              "Do NOT output JSON code blocks. Write the report in English.\n\n"
            + f"Schedule data: {schedule}\n"
            + f"Supplies data: {logistics}"
        )
    else:
        msg = (
            _OPS_PREFIX["zh"]
            + "任务：你当前以财务科身份运行。根据排班和物资计划生成运营成本预估。\n"
            + "使用 finance-query 技能查询财务数据。\n"
            + "操作步骤：\n"
            + "1. 读取 /opt/openclaw/skills/custom/finance-query/SKILL.md\n"
            + "2. 分析人力成本（根据排班数据估算）\n"
            + "3. 分析物资成本（根据配送计划估算）\n"
            + "4. 输出运营成本预估报告\n"
            + "输出格式：Markdown 报告（标题+分节+表格），禁止输出 JSON 代码块。\n\n"
            + f"排班数据：{schedule}\n"
            + f"物资数据：{logistics}"
        )
    return AgentTask(agent_id=agent_id, message=msg)


def _prepare_director_report(input: dict[str, Any], outputs: dict[str, Any]) -> AgentTask:
    agent_id = _resolve_agent(input, "director_agent_id", "director")
    schedule = _ctx(outputs.get("nursing-schedule-step", ""))
    logistics = _ctx(outputs.get("logistics-step", ""))
    finance = _ctx(outputs.get("finance-step", ""))
    if _lang() == "en":
        msg = (
            _OPS_PREFIX["en"]
            + "Task: as the director, synthesize all department outputs into "
              "this week's consolidated operations report.\n"
            + "Use the report-generate skill to produce the report.\n"
            + "Steps:\n"
            + "1. Read /opt/openclaw/skills/custom/report-generate/SKILL.md\n"
            + "2. Synthesize the schedule, supplies, and cost data into a weekly "
              "report\n"
            + "3. The report must contain: schedule overview, supplies delivery, "
              "cost estimate, key concerns\n"
            + "Output format: a Markdown report (title + sections + tables). "
              "Do NOT output JSON code blocks. Write the report in English.\n\n"
            + f"Schedule data: {schedule}\n"
            + f"Supplies data: {logistics}\n"
            + f"Cost data: {finance}"
        )
    else:
        msg = (
            _OPS_PREFIX["zh"]
            + "任务：作为院长，汇总所有部门的输出，生成本周运营综合报表。\n"
            + "使用 report-generate 技能生成报表。\n"
            + "操作步骤：\n"
            + "1. 读取 /opt/openclaw/skills/custom/report-generate/SKILL.md\n"
            + "2. 综合排班、物资、成本数据生成周报表\n"
            + "3. 报表需包含：排班概况、物资配送、成本预估、重点关注事项\n"
            + "输出格式：Markdown 报告（标题+分节+表格），禁止输出 JSON 代码块。\n\n"
            + f"排班数据：{schedule}\n"
            + f"物资数据：{logistics}\n"
            + f"成本数据：{finance}"
        )
    return AgentTask(agent_id=agent_id, message=msg)


# --- Flow definition ---

nursing_ops_flow = Flow(
    id="nursing.ops",
    version="1.1.0",
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
