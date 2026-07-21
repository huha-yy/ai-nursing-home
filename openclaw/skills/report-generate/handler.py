"""Report generation handler for the 院长 (Director) Agent.

Synthesizes a weekly operations report by aggregating data from:
- 护理科 (nursing schedule)
- 总务科 (logistics / deliveries)
- 财务科 (cost estimate)

In the MVP, the LLM handles the actual report text generation.
This handler prepares the structured data skeleton that the LLM fills in.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any


def _today_week_range() -> tuple[str, str]:
    """Return (monday, sunday) ISO dates for the current week."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


async def generate_weekly_report(
    db_url: str,
    schedule_data: str = "",
    logistics_data: str = "",
    finance_data: str = "",
) -> dict[str, Any]:
    """Synthesize a weekly ops report.

    For MVP, this handler prepares the data structure. The calling LLM
    fills in the actual narrative content based on the aggregated data.

    Args:
        db_url: Postgres connection string (for DB-backed enrichment in future).
        schedule_data: JSON string of nursing schedule output.
        logistics_data: JSON string of logistics/delivery plan output.
        finance_data: JSON string of finance cost estimate output.

    Returns:
        A structured report dict with section placeholders.
    """
    week_start, week_end = _today_week_range()

    # Parse input data if provided as JSON strings
    def _safe_parse(raw: str) -> dict[str, Any]:
        if not raw or not raw.strip():
            return {}
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            return parsed if isinstance(parsed, dict) else {"raw": raw}
        except (json.JSONDecodeError, TypeError):
            return {"raw": str(raw)[:2000]}

    schedule = _safe_parse(schedule_data)
    logistics = _safe_parse(logistics_data)
    finance = _safe_parse(finance_data)

    # Build the report skeleton
    report: dict[str, Any] = {
        "report_type": "weekly_ops",
        "title": "养老院运营周报",
        "period": f"{week_start} - {week_end}",
        "generated_at": date.today().isoformat(),
        "sections": {
            "排班概况": {
                "title": "排班概况",
                "description": "本周各楼栋护工排班统计",
                "data": schedule,
                "highlights": _extract_schedule_highlights(schedule),
            },
            "物资配送": {
                "title": "物资配送",
                "description": "本周物资配送计划与库存预警",
                "data": logistics,
                "highlights": [],
            },
            "成本预估": {
                "title": "成本预估",
                "description": "本周运营成本预估（人力+物资）",
                "data": finance,
                "highlights": [],
            },
            "重点关注": {
                "title": "重点关注事项",
                "description": "需院长关注的风险和待办事项",
                "items": [
                    "检查库存预警物资（见物资配送章节）",
                    "确认排班覆盖率是否达标",
                    "审核运营成本是否在预算范围内",
                ],
            },
        },
    }

    return report


def _extract_schedule_highlights(schedule: dict[str, Any]) -> list[str]:
    """Extract key highlights from schedule data."""
    highlights: list[str] = []
    if schedule:
        staff = schedule.get("staff_count")
        if staff:
            highlights.append(f"本周参与排班护工：{staff}人")
        day_s = schedule.get("day_shifts")
        night_s = schedule.get("night_shifts")
        if day_s is not None and night_s is not None:
            highlights.append(f"白班{day_s}个，夜班{night_s}个")
        building = schedule.get("building")
        if building:
            highlights.append(f"覆盖楼栋：{building}")
    return highlights
