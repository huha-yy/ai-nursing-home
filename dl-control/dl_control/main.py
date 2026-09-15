"""FastAPI app factory + lazy ASGI entrypoint."""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import structlog
from dl_shared.rate_limit import RateLimitMiddleware
from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from redis.asyncio import Redis
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request as _Request
from starlette.responses import RedirectResponse as StarletteRedirect

from dl_control import i18n
from dl_control.auth import routes as auth_routes
from dl_control.auth.errors import MustRotatePasswordError
from dl_control.auth.middleware import COOKIE_NAME as _NURSING_COOKIE
from dl_control.auth.middleware import require_password_rotated
from dl_control.auth.sessions import SessionStore
from dl_control.db import Database
from dl_control.logging import configure_logging
from dl_control.settings import load_settings

PACKAGE_DIR = Path(__file__).parent


def _i18n_context(request: _Request) -> dict:
    lang = i18n.normalize_lang(request.cookies.get(i18n.LANG_COOKIE))
    return {
        "lang": lang,
        "html_lang": i18n.HTML_LANG[lang],
        "t": i18n.translator(lang),
    }


def _page_i18n(request: _Request, prefixes: tuple[str, ...]) -> str:
    """Cookie-lang JSON blob for a page's inline scripts (see i18n.dump_prefixed)."""
    lang = i18n.normalize_lang(request.cookies.get(i18n.LANG_COOKIE))
    return i18n.dump_prefixed(lang, prefixes)


def _req_lang(request: _Request) -> str:
    """chat 语义层语言（P4 英文问答）：读 lang cookie 归一，缺省 zh。

    与 i18n.DEFAULT_LANG（en，界面文案兜底）刻意不同——chat 的预取数据、
    会话历史、agent 上下文现状是中文，cookie 未设置时对话行为必须保持中文。
    """
    raw = request.cookies.get(i18n.LANG_COOKIE)
    return raw if raw in i18n.LANGS else "zh"


TEMPLATES = Jinja2Templates(
    directory=str(PACKAGE_DIR / "templates"),
    context_processors=[_i18n_context],
)


class _NursingWorkflowStart(BaseModel):
    """Request body for triggering the multi-agent nursing ops workflow."""

    building: str | None = None  # 缺省=会话楼栋，再缺省按库实值（P5b 双语）
    nursing_agent_id: str | None = None
    logistics_agent_id: str | None = None
    general_agent_id: str | None = None
    director_agent_id: str | None = None


# ── nursing-erp 调用辅助（模块级，便于单测）────────────────────────


def _erp_headers(sess=None) -> dict:
    """nursing-erp /api/ 调用头。

    X-API-Key：ERP 自 2026-08-21 起强制机器调用认证。
    X-Building：可选楼栋范围（阶段一第二批）——楼长会话带本楼名，
    ERP 只返回该楼数据；无楼栋会话（管理层 u001-u006）不发头 → 全院。
    楼栋名是中文，HTTP 头只可靠传 ASCII：percent-encode（quote）后发送，
    ERP 侧 unquote 还原（httpx 对非 ASCII 头值直接 UnicodeEncodeError）。
    X-Family-Token：家属会话（role="family"）——ERP /api/family/* 靠它
    定位 FamilyMember 并只放行绑定老人；token 是 secrets.token_hex 的
    ASCII，无需编码。家属会话无 building，两个头互斥不叠加。
    """
    key = os.environ.get("NURSING_ERP_API_KEY", "")
    headers = {"X-API-Key": key} if key else {}
    building = ((getattr(sess, "building", "") or "") if sess else "").strip()
    if building:
        headers["X-Building"] = quote(building)
    family_token = ((getattr(sess, "family_token", "") or "") if sess else "").strip()
    if family_token:
        headers["X-Family-Token"] = family_token
    return headers


def _week_start() -> str:
    """本周周一日期 YYYY-MM-DD（ERP 周菜单的 week_start 键）。"""
    today = datetime.now().date()
    return (today - timedelta(days=today.weekday())).isoformat()


async def _default_workflow_building(conn) -> str:
    """周报 workflow 的楼栋兜底值——从库实值取（P5b 楼栋英文化）。

    院长/管理层会话无 building，此前写死 "3号楼"；en 演示期
    nursing_schedules.building 已改 "Building 3"，写死值会查空排班。
    优先 3号楼/Building 3（历史默认），都无则取任一非空值，最后回落字面量。
    """
    cur = await conn.execute(
        "SELECT building FROM nursing_schedules "
        "WHERE building IN ('3号楼', 'Building 3') "
        "ORDER BY CASE building WHEN '3号楼' THEN 0 ELSE 1 END LIMIT 1"
    )
    row = await cur.fetchone()
    if row and row[0]:
        return row[0]
    cur = await conn.execute(
        "SELECT building FROM nursing_schedules "
        "WHERE building IS NOT NULL AND building <> '' LIMIT 1"
    )
    row = await cur.fetchone()
    return row[0] if row and row[0] else "3号楼"


def _erp_items(data) -> list:
    """从 ERP API 响应中提取行，供 chat 技能预取注入（模块级，便于单测）。

    - 分页 dict（{"items": [...], "count": N}）→ items 列表
    - 聚合 dict（{"rows": [...], 汇总标量}，如 /api/billing/arrears/）→
      汇总标量置顶成一行 + rows —— LLM 不丢 total_outstanding 这类总数
    - 纯标量 dict（如 /api/billing/summary/ 三额勾稽）→ 包成单行列表
    - 其余：list 原样，标量/None → 空列表
    """
    if isinstance(data, dict):
        for key in ("items", "rows"):
            if isinstance(data.get(key), list):
                if key == "rows":
                    scalars = {k: v for k, v in data.items()
                               if not isinstance(v, (list, dict))}
                    if scalars:
                        return [scalars, *data[key]]
                return data[key]
        return [data]
    return data if isinstance(data, list) else []


# ── 大屏增强辅助（2026-08-25；模块级，便于单测）──────────────────

_MEAL_ORDER = ("早餐", "午餐", "晚餐")
_ORDER_STATUS_DISPLAY = {
    "ordered": "已点", "modified": "已改", "preparing": "备餐中",
    "delivering": "配送中", "delivered": "已送达", "cancelled": "已退",
}
_CARE_LEVEL_ORDER = ("自理", "半护", "全护", "失智", "特护", "未定")


def _today_cn() -> str:
    """ERP WeekMenu.day 的中文星期取值（周一…周日）。"""
    return "周" + "一二三四五六日"[datetime.now().weekday()]


def _today_menu(week_rows: list) -> list[dict]:
    """week-menu 全周行 → 今日三餐 [{meal_type, dishes:[{name, category}]}]。

    按 早/午/晚 排序；category 原样透传（ERP 取值域 主食/汤/素菜/荤菜/小菜，
    前端按类别给菜签配色，未知值走中性色兜底）。
    """
    order = {"早餐": 0, "午餐": 1, "晚餐": 2}
    rows = [
        {
            "meal_type": m.get("meal_type", ""),
            "dishes": [
                {"name": d.get("name", ""), "category": d.get("category", "")}
                for d in m.get("dishes", [])
                if d.get("name")
            ],
        }
        for m in week_rows
        if m.get("day") == _today_cn()
    ]
    return sorted(rows, key=lambda r: order.get(r["meal_type"], 9))


def _order_stats(per_meal: dict) -> dict:
    """按餐次分拉回来的今日订单 → 餐次×状态统计 + 特殊餐计数。

    total 口径 = 除 cancelled 外全部（退餐不算今日餐单）；by_status 保留
    退餐计数——大屏要让厨房/院长看到退改痕迹，前端灰字展示。
    special 同理只数未退订单（退了的特殊诉求不该再让厨房操心）。
    per_meal 只认 _MEAL_ORDER 里的餐次，键序即输出序。
    """
    meals, by_status_all = [], {}
    total = special_total = 0
    for mt in _MEAL_ORDER:
        by_status: dict[str, int] = {}
        special = 0
        for i in per_meal.get(mt, []):
            st = i.get("status", "")
            by_status[st] = by_status.get(st, 0) + 1
            if st != "cancelled" and (i.get("special_requests") or "").strip():
                special += 1
        valid = sum(n for st, n in by_status.items() if st != "cancelled")
        meals.append({"meal_type": mt, "total": valid, "special": special,
                      "by_status": by_status})
        total += valid
        special_total += special
        for st, n in by_status.items():
            by_status_all[st] = by_status_all.get(st, 0) + n
    return {"meals": meals, "total": total, "special": special_total,
            "by_status": by_status_all}


def _care_level_pie(residents: list) -> list[dict]:
    """residents 列表 → echarts 饼图数据 [{name, value}]，按档位顺序排列。

    空 care_level 归"未定"；表外新增档位（如拍脑袋加的"特护2"）追加在
    尾部而不是丢弃——分布图宁多勿缺。
    """
    counts: dict[str, int] = {}
    for r in residents:
        lv = (r.get("care_level") or "").strip() or "未定"
        counts[lv] = counts.get(lv, 0) + 1
    rows = [{"name": k, "value": counts.pop(k)} for k in _CARE_LEVEL_ORDER if k in counts]
    rows += [{"name": k, "value": v} for k, v in counts.items()]
    return rows


def _occupancy_summary(buildings: list) -> dict:
    """beds/occupancy 楼栋行 → 合计 {total, occupied, free, rate}。

    rate 为 0-100 整数百分比（前端直接拼字），无床位 None。
    楼长会话带 X-Building 时 ERP 只回本楼行——副行语义随之变成本楼入住率，
    无需这边区分。
    """
    total = sum(b.get("total", 0) for b in buildings)
    occupied = sum(b.get("occupied", 0) for b in buildings)
    free = sum(b.get("free", 0) for b in buildings)
    rate = round(occupied / total * 100) if total else None
    return {"total": total, "occupied": occupied, "free": free, "rate": rate}


def _alert_display(i: dict, lang: str) -> dict:
    """ERP /api/incidents/ 行 → /alerts 页载荷行（P5b，模块级便于单测）。

    lang=en 时枚举字段过 i18n.enum_display：category（取 category_display，
    ERP 默认 zh-hans → 中文"摔倒"等）与 severity_display 换英文；name/
    building/content（自由文本）原样。lang=zh 时零改动。
    """
    return {
        "id": i["id"],
        "name": i.get("resident_name", ""),
        "building": i.get("building", ""),
        "category": i18n.enum_display(
            i.get("category_display", i.get("category", "")), lang),
        "severity": i.get("severity", ""),
        "severity_display": i18n.enum_display(i.get("severity_display", ""), lang),
        "content": i.get("description", ""),
        "handled": bool(i.get("handled", False)),
        "handled_by": i.get("handled_by", "") or "",
        "handled_at": i.get("handled_at", "") or "",
        "created_at": i.get("created_at", ""),
    }


def _today_activities(rows: list) -> dict:
    """nursing_activities 行 (date, title, time, location) → {date, items}。

    SQL 已按 _eff_date 选定日期（当天无数据取最近一天），这里只负责
    成形：time 文本升序（"09:00-…" 字典序即时间序）、time 为空垫最后、
    title 透传。date 供前端标注"实际是哪天的安排"（演示数据可能停在
    最近一天，口径要诚实）。
    """
    items = sorted(
        ({"title": r[1], "time": r[2] or "", "location": r[3] or ""} for r in rows),
        key=lambda x: (x["time"] == "", x["time"]),  # 空时间垫最后
    )
    return {"date": str(rows[0][0]) if rows else None, "items": items}


def _split_report_text(text: str) -> dict:
    """agent 输出 → {text: 报告正文, process: 过程旁白}。

    OpenClaw 把工具调用旁白（"📋 已读取技能说明… / ⚠️ 缺少依赖…"）和最终
    报告拼在同一串 payload 文本里。正文 = 从首个 markdown 标题行（#/##/###）
    起；标题前的旁白与报告尾部混入的"执行摘要：…"归 process，前端折叠
    展示（raw 字段仍全量留档，审计不受影响）。无标题行时整体当正文返回。

    报告尾部的"输出 JSON（供下一部门使用）"段是 agent 间机器交接数据，
    不是人读内容——从展示中整体剥除（heading 形式或收尾 ```json 块），
    同样只影响展示，raw 留档不动。
    """
    m = re.search(r"(?m)^#{1,3} ", text)
    if not m:
        return {"text": text[:8000], "process": None}
    head = text[: m.start()].strip()
    body = text[m.start():].strip()
    m2 = re.search(r"(?m)^执行摘要[：:]", body)
    tail = ""
    if m2:
        tail = body[m2.start():].strip()
        body = body[: m2.start():].strip()
    # 剥除尾部机器交接 JSON 段：优先按"输出 JSON"标题切；无标题时认裸
    # ```json 块——块后只剩一两句收尾话（≤120 字，如"以上结果可直接作为
    # 下一部门的输入"）或为空，则整段剥除；后面还有实质内容则当正文引用
    # 不动（从最后一个满足条件的块往前找）
    m3 = re.search(
        r"(?m)^(?:#{1,3}\s*)?(?:\d+|[一二三四五六七八九十]+)?[、.]?\s*输出\s*JSON.*$",
        body,
    )
    if m3:
        body = body[: m3.start()].strip()
    else:
        for m3 in reversed(list(re.finditer(r"(?ms)^```json\s*.*?^```[ \t]*$", body))):
            if len(body[m3.end():].strip()) <= 120:
                body = body[: m3.start()].strip()
                break
    process = "\n\n".join(p for p in (head, tail) if p)
    # 收尾裸 ``` 围栏线：agent 偶尔把整份报告包进 ```markdown 块——首行围栏
    # 已随 head 归旁白，尾线会漏进正文渲染成字面反引号，剥掉
    body = re.sub(r"(?ms)\n?```\s*\Z", "", body).strip()
    return {
        "text": body[:8000] or None,
        "process": process[:3000] if process else None,
    }


def _skill_queries() -> list:
    """意图关键词 → 预取查询映射（每次调用重建，日期保持新鲜）。

    meal-query 2026-08-21 修复：原先指向不存在的 /api/meal-plans/
    （自上线起静默 404），改为本周周菜单。

    finance-query 2026-08-24 升级：财务问答接入 ERP 应收月账单
    /api/billing/ —— 欠费类 → 欠费名单（跨月累计，首行带总数），
    泛财务词 → 当月三额汇总；餐费/月结仍走 /api/meal-finance/。
    行序即优先级（首匹配即 break）：欠费行必须排在泛财务行之前。

    assessment 2026-08-24 接入：入住评估→定级查询。两行都必须排在
    logistics（"盘点"）与 resident-query（"老人/健康档案"）行**之前**——
    "评估盘点/老人的评估/谁该复评"这类问句同时含两边关键词，首匹配即
    break，先命中评估行才有评估数据；行内再分：盘点类（待评估/复评）→
    review 三态盘点，泛评估词 → 评估单列表。

    2026-09-15 P4（广交会英文问答）：每行追加英文关键词（全小写，
    _match_skill_rows 对 message.lower() 做子串匹配，中文不受 lower()
    影响）。演示铁律对英文同样成立——英文问句必须命中意图行才有真实
    数据注入，否则 LLM 编数。撞车口径与中文行对齐：异常事件行先于
    resident（elderly）行；欠费 → 餐费 → 泛财务行序保持；bed/occupancy
    归床位行，resident 行收 how many residents/elderly。
    """
    return [
        # 「当班」收进排班行（09-07 三轮：护理台快速按钮「某楼栋当班人员」原先
        # 半命中员工行——排班行在前，注入的是排班表而非花名册，正是该问句要的）
        (["排班", "值班", "谁当班", "当班", "排班表",
          "schedule", "shift", "on duty", "duty", "roster", "staffing"], "nursing-schedule",
         "API:/api/schedules/?date=" + datetime.now().strftime("%Y-%m-%d")),
        (["工单", "完成率", "护理完成", "任务完成",
          "work order", "task completion", "completion rate"], "nursing-work-order",
         "API:/api/incidents/"),
        # 评估两行须在 logistics（"盘点"）与 resident（"老人"）行之前：
        # "评估盘点/老年人的评估" 同时含对方关键词，先命中这里才有评估数据
        (["待评估", "复评", "评估盘点", "review due", "re-assessment", "reassessment"],
         "assessment-query",
         "API:/api/assessments/review/"),
        (["评估", "定级", "能力等级", "护理等级", "assessment", "care level", "grading"],
         "assessment-query",
         "API:/api/assessments/"),
        (["库存", "盘点", "物资", "采购", "尿不湿", "手套", "口罩", "消毒液", "胃管", "护理垫",
          "inventory", "stock", "supplies", "diapers", "gloves", "mask", "purchase"],
         "logistics-inventory", "API:/api/inventory/"),
        # 异常事件口语（摔倒/走失/发烧…）必须在 resident（"老人/elderly"）行之前：
        # "有老人摔倒吗"同时含"老人"，先命中这里才拿得到 incidents 而非老人名单
        (["摔倒", "走失", "坠床", "噎食", "发烧", "发热",
          "alert", "incident", "fall", "fell", "missing", "choking", "fever"],
         "alert-query",
         "API:/api/incidents/?handled=false"),
        # 财务三行 + 投诉行必须在 resident 行之前（09-07 三轮重排：resident 行
        # 收进真实人名后，「吴桂英欠费」「家属有意见」这类 人名/名词+意图 组合
        # 问句会被 resident 行吞掉——欠费/费用/投诉是更具体的意图，前置）；
        # 行内序：欠费 → 餐费/月结 → 泛财务（首匹配 break，欠费须最先）
        (["欠费", "没交", "未缴", "未交", "催缴", "arrears", "unpaid", "overdue"],
         "finance-query",
         "API:/api/billing/arrears/"),
        (["餐费", "月结", "meal fee"], "finance-query", "API:/api/meal-finance/"),
        (["费用", "结算", "缴费", "账单", "应收", "出账", "收费", "收了",
          "bill", "fee", "payment", "charge", "invoice", "settlement", "cost"],
         "finance-query",
         "API:/api/billing/summary/"),
        # 投诉/意见（09-07 三轮：「有人投诉吗」未命中任何行；表在本地 PG，
        # 3 行真实演示数据，无日期列按 id 倒序）。须在 meal（"食堂"）行
        # 之前——「食堂有投诉吗」该给投诉数据而非菜单
        (["投诉", "抱怨", "意见", "complaint", "complain", "feedback"], "complaint-query",
         "SELECT id, content, source, status FROM nursing_complaints "
         "ORDER BY id DESC LIMIT 20"),
        # 「入住」这类短词刻意不收：会先于后行吞掉"入住率/床位"类问句。
        # 收长词组（09-07 实测坑：销售问「院里住了多少人」未命中任何行，
        # agent 拿系统 prompt 里的对标口径编出 1100 人）；「入住情况/入住动态」
        # 走 residents 列表（自带 admission_date，可判断近期新入住；离院
        # 台账 ERP 未暴露 API，回答由 agent 如实说明）。
        # 人名（09-07 三轮：原"张建国"是陈旧种子名，库里根本没有——换成
        # nursing_residents 实有 8 人；组合问句由前方的 欠费/摔倒 行优先，
        # 人名行只兜纯人名问句「张国栋住哪」）
        # 2026-09-15 P5：追加 ERP --lang en 重灌后的英文名（zh 名保留——
        # 双语演示同一映射行兜底，英文名与 rebuild_demo_data.py
        # NAME_ZH_EN 的拼音拼写一一对应）
        (["老人", "张国栋", "李秀兰", "陈永发", "赵玉芬", "王淑珍", "刘明德",
          "吴桂英", "周德胜", "301", "302", "303", "108", "205", "老人档案", "健康档案",
          "住了多少人", "多少入住", "入住人数", "在院人数", "在院老人数",
          "入住情况", "入住动态", "resident", "elderly", "how many residents",
          "Zhang Guodong", "Li Xiulan", "Chen Yongfa", "Zhao Yufen",
          "Wang Shuzhen", "Liu Mingde", "Wu Guiying", "Zhou Desheng"],
         "resident-query", "API:/api/residents/"),
        # 床位/入住率走 beds occupancy（resident 行刻意不收裸"入住"给它让路）
        (["床位", "入住率", "空床", "满床", "几床", "空着",
          "bed", "occupancy", "vacant", "available beds"], "beds-occupancy",
         "API:/api/beds/occupancy/"),
        # 早/午/晚饭口语（"晚饭吃什么"此前未命中任何行）；「食堂」（09-07
        # 三轮：「食堂今天做了什么」未命中任何行）。英文用 " eat"/"eating"
        # 而非裸 "eat"——后者撞 heat/weather（"heat stroke alert" 会被吞成菜单）
        (["菜单", "饭菜", "今天吃什么", "伙食", "早餐", "午餐", "晚餐",
          "早饭", "午饭", "晚饭", "夜宵", "晚上吃什么", "早上吃什么", "中午吃什么",
          "吃什么", "食堂",
          "menu", "meal", "food", "breakfast", "lunch", "dinner", "supper", " eat", "eating"],
         "meal-query",
         f"API:/api/week-menu/?week_start={_week_start()}"),
        (["活动", "文娱", "合唱", "讲座", "棋牌", "书法",
          "activity", "activities", "event", "entertainment"], "activity-query",
         # 口径对齐 dashboard _eff_date 兜底：演示活动数据常停在最近一天，
         # 纯 date >= CURRENT_DATE 会空手（09-07 三轮实测「菜单和活动」答
         # "活动数据为空"）。今天或未来有数据取之；否则取最近一天那天起。
         "SELECT title, date, time, location FROM nursing_activities "
         "WHERE date >= COALESCE((SELECT MIN(date) FROM nursing_activities "
         "WHERE date >= CURRENT_DATE), (SELECT MAX(date) FROM nursing_activities)) "
         "ORDER BY date, time LIMIT 10"),
        (["预警", "告警", "重点关注", "异常", "warning", "alert"], "alert-query",
         "API:/api/incidents/?handled=false"),
        # 员工口语（护工/护士/医生/护理员——"护理员"不含"护理等级"，评估行不吞）
        (["员工", "谁负责", "人员", "值班人员", "护工", "护士", "医生", "护理员",
          "staff", "employee", "caregiver", "nurse", "doctor", "who is on"],
         "staff-query", "API:/api/employees/"),
    ]


# 注入提示词的回答风格契约（2026-09-07 收紧）：问事实答事实，问分析才分析。
# 此前无任何简洁约束，「晚饭吃什么」答 1350 字/7 张表/结尾"请院长指示"——
# 未要求的展开与固定邀约在演示里是冷场点，也拖垮流式节奏（紧回答 ~3s 完事）。
_PROMPT_ANSWER_RULES = (
    "请根据以上真实数据回答，不要编造。回答风格要求："
    "第一句话直接给出答案；数据是多行时用一张简洁表格呈现；"
    "只回答用户问到的内容，不要主动展开没问到的分析、建议或延伸话题；"
    "结尾不要询问是否需要进一步帮助、不要罗列可代办事项；"
    "仅当用户明确要求建议或分析时才展开。"
)

# P4 英文版（内容对等：先给事实/多行用简洁表格/只答所问/不邀约/问了才展开）
_PROMPT_ANSWER_RULES_EN = (
    "Answer based on the real data above; do not make things up. Answer style: "
    "give the answer directly in the first sentence; when the data has multiple "
    "rows, present it in one concise table; answer only what the user asked — "
    "do not volunteer unrequested analysis, suggestions, or side topics; do not "
    "end by asking whether further help is needed or listing things you could "
    "do; expand only when the user explicitly asks for advice or analysis."
)


def _prompt_answer_rules(lang: str = "zh") -> str:
    """回答风格契约的语言分支（agent 注入与非流式/流式 prompt 共用）。"""
    return _PROMPT_ANSWER_RULES_EN if lang == "en" else _PROMPT_ANSWER_RULES


def _agent_en_prefix() -> str:
    """英文会话发往 agent 的载荷前缀（P4 语言指令 + P5b 枚举术语翻译指令）。

    术语词表由 i18n.enum_terms_clause 从 ENUM_ZH_EN 生成，与页面显示映射
    同源，两处不会漂移。非流式/流式两个注入点共用。
    """
    return "[Please respond in English.] " + i18n.enum_terms_clause() + "\n"


def _schedule_window(message: str) -> list[str] | None:
    """排班问句的时间范围词 → 日期列表（升序、含今天）；None=只查当天。

    2026-09-07 实测坑：「最近三天排班情况」原先只注入当天数据，agent 把
    其余天数断言成"没有数据"（假阴性——ERP/PG 两边其实都有）。ERP
    /api/schedules/ 只支持单日参数，这里在预取层把范围问句展开成多日。
    「上周」刻意不展开（语义是上一个自然周， trailing 窗口会给错日期，
    交给 agent 自己的工具查）。

    2026-09-07 用户实测「明天后天呢」失败：ERP 排班已铺到 09-30
    （--cover-until），但这里没有未来词 → 只注入当天 → agent 诚实报
    "不含明天的数据"（假阴性，数据其实在）。未来词分支放在过去词之前
    （「明后天」同时含"明/后天"子串，先整词后单词）。

    2026-09-15 P4：英文时间词并入对应分支（tomorrow/day after tomorrow/
    next 3 days/this week/last 3 days/recent），语义与中文分支一致。
    "day after tomorrow" 含子串 "tomorrow"，必须先于 tomorrow 判定；
    中文侧「后天」上移到「明天」之前对行为无影响（组合词已在首分支拦下）。
    """
    msg = message.lower()
    today = datetime.now().date()
    if any(w in msg for w in ("明天后天", "明后天")):
        return [(today + timedelta(days=i)).isoformat() for i in (1, 2)]
    if any(w in msg for w in ("后天", "day after tomorrow")):
        return [(today + timedelta(days=2)).isoformat()]
    if any(w in msg for w in ("明天", "tomorrow")):
        return [(today + timedelta(days=1)).isoformat()]
    if any(w in msg for w in ("未来", "接下来", "next 3 days", "next three days")):
        return [(today + timedelta(days=i)).isoformat() for i in range(3)]
    if any(w in msg for w in ("本周", "这周", "一周", "7天", "this week")):
        monday = today - timedelta(days=today.weekday())
        days = (today - monday).days + 1
        return [(monday + timedelta(days=i)).isoformat() for i in range(days)]
    if any(w in msg for w in ("三天", "3天", "最近", "过去", "几天", "昨天", "前天",
                              "last 3 days", "last three days", "past few days", "recent")):
        return [(today - timedelta(days=2 - i)).isoformat() for i in range(3)]
    return None


# 组合问句白名单（09-07 三轮）：快速按钮「本日菜单和活动」类问句同时含
# 两组关键词，此前首匹配 break 只注入一半数据。只放行已观察到的组合对——
# 任意两行并注会带来撞车噪声（「评估盘点」assessment 行 + "盘点"子串命中
# logistics 行 → 莫名注入一页库存），新组合确认无撞车再进白名单。
_COMBO_SKILL_PAIRS = {frozenset({"meal-query", "activity-query"})}
_COMBO_LABELS = {"meal-query": "菜单数据", "activity-query": "活动数据"}

# 院长周报结构化摘要的 4 个段标题（P4：lang=en 时翻成英文下发）
_REPORT_SECTIONS_EN = {
    "排班概况": "Schedule Overview",
    "物资配送": "Supplies Delivery",
    "成本预估": "Cost Estimate",
    "重点关注": "Key Concerns",
}


def _match_skill_rows(message: str, table: list) -> list[tuple[str, str]]:
    """意图匹配：返回 (skill_name, sql) 列表——首匹配行 + 白名单组合的第二行。

    单意图问句恒只返回 1 行（与旧 first-match-break 行为一致）；组合问句
    最多 2 行，且第二行必须与首行构成白名单组合对。
    匹配对 message.lower() 做子串判定（P4）：英文关键词全小写，
    中文关键词不受 lower() 影响。
    """
    msg = message.lower()
    matches: list[tuple[str, str]] = []
    for keywords, skill_name, sql in table:
        if not any(kw in msg for kw in keywords):
            continue
        if not matches:
            matches.append((skill_name, sql))
        elif len(matches) == 1 and frozenset(
            (matches[0][0], skill_name)
        ) in _COMBO_SKILL_PAIRS:
            matches.append((skill_name, sql))
            break
    return matches


async def _prefetch_skill_data(sql, skill_name, message, sess, db) -> list | None:
    """单个意图行的预取执行（非流式/流式两个消费点共用的 helper）。

    排班行命中时间范围词 → 多日逐日拉取合并（字段精简到
    date/employee_name/shift/building，7 天窗口 ~168 行不至于撑爆注入上限）。
    其余行走原单查询路径（API:→ERP，SQL→本侧 PG）。任何失败返回 None。
    """
    import httpx

    erp = os.environ.get("NURSING_ERP_URL", "http://192.168.10.247:9081")
    try:
        if skill_name == "nursing-schedule" and sql.startswith("API:/api/schedules/"):
            dates = _schedule_window(message)
            if dates:
                rows: list = []
                per_day: dict = {}
                async with httpx.AsyncClient(timeout=10.0, headers=_erp_headers(sess)) as cli:
                    for d in dates:
                        resp = await cli.get(f"{erp}/api/schedules/?date={d}")
                        if resp.status_code == 200:
                            items = [
                                {k: it.get(k) for k in ("date", "employee_name", "shift", "building")}
                                for it in _erp_items(resp.json())
                            ]
                            rows.extend(items)
                            cnt: dict = {"total": len(items)}
                            for it in items:
                                cnt[it["shift"]] = cnt.get(it["shift"], 0) + 1
                            per_day[d] = cnt
                # 汇总置顶一行（欠费行同款范式）：LLM 自己数多行表格会漏人
                # （实测 09-07：3 天 × 24 行注入无误，回答却报 23/22）
                summary = {"每日排班汇总": per_day}
                return [summary] + rows[:99] if rows else None
        if sql.startswith("API:"):
            async with httpx.AsyncClient(timeout=10.0, headers=_erp_headers(sess)) as cli:
                resp = await cli.get(f"{erp}{sql[4:]}")
            return _erp_items(resp.json())[:50] if resp.status_code == 200 else None
        async with db.conn(user_id=None, role="system") as conn:
            cur = await conn.execute(sql)
            rows2 = await cur.fetchall()
            cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in rows2][:50]
    except Exception as e:
        logging.getLogger(__name__).warning(f"Skill {skill_name} query failed: {e}")
        return None


# 追问碎片时间词：当前句一个意图关键词都不含、但含时间指代——典型如
# 「明天后天呢」（对着上一句排班问句的追问）。此时沿用上一句用户消息的
# 意图、用本句的时间词重新取数；没有这层，追问轮零注入，agent 只能拿
# 上一轮的旧注入诚实说"查不到"（09-07 用户实测踩中）。
# 护栏：仅当本句无任何命中时才回退——「明天吃什么」自己命中菜单行，
# 不会被带回排班。
_FOLLOWUP_TIME_WORDS = (
    "明天", "后天", "今天", "今晚", "本周", "这周", "上周", "下周",
    "最近", "前几天", "接下来",
    # P4 英文追问碎片（"and tomorrow?"）
    "tomorrow", "today", "this week", "last week", "next week", "recent",
)


async def _collect_skill_data(
    message: str, sess, db, is_family: bool, history: list | None = None
):
    """两个 chat 端点共用的意图匹配+预取（09-07 三轮统一入口）。

    单意图 → 该行数据的列表；组合问句 → {中文标签: 行} dict（两个意图
    的数据并排可辨）；未命中或全部拉取失败 → None。
    """
    table = _family_skill_queries() if is_family else _skill_queries()
    matched = _match_skill_rows(message, table)
    if not matched and history and any(w in message for w in _FOLLOWUP_TIME_WORDS):
        for entry in reversed(history):
            if entry.get("role") == "user":
                matched = _match_skill_rows(entry.get("content", ""), table)
                break
    results: list[tuple[str, list]] = []
    for sname, ssql in matched:
        r = await _prefetch_skill_data(ssql, sname, message, sess, db)
        if r is not None:
            results.append((sname, r))
    if not results:
        return None
    if len(results) == 1:
        return results[0][1]
    return {_COMBO_LABELS.get(s, s): r for s, r in results}


def _family_skill_queries() -> list:
    """家属会话（role="family"）专用意图映射 —— 全部走 ERP /api/family/*。

    与员工版三处刻意不同：
    - 只有 API: 行，没有 SQL 行 —— 本侧库（agents/workflows 等）没有
      家属可看的数据，SQL 预取对家属是越权面；
    - ERP 侧按 X-Family-Token 过滤，返回的永远只是绑定老人；
    - 行序即优先级（首匹配即 break）：账单/吃饭在前，泛"老人近况"兜底最后。

    2026-09-15 P4：每行追加英文关键词（全小写，子串匹配同员工版）。
    """
    return [
        (["账单", "费用", "缴费", "欠费", "收费", "月结", "钱",
          "bill", "fee", "payment", "charge"], "family-billing",
         "API:/api/family/billing/"),
        (["吃饭", "点餐", "订餐", "退餐", "伙食", "三餐", "菜单", "饭菜", "吃什么",
          "meal", "order", "food", "menu", " eat", "eating"],
         "family-meals", "API:/api/family/meals/"),
        (["健康", "身体", "血压", "血糖", "用药", "护理", "评估", "病历", "过敏", "诊断",
          "health", "blood pressure", "medication", "care"],
         "family-care", "API:/api/family/care/"),
        # 兜底行：泛问老人近况 → 总览（基础+评估+近期动态+今日三餐+欠费）
        (["老人", "爸", "妈", "近况", "怎么样", "状态", "情况", "照护", "住",
          "parent", "father", "mother", "how is", "condition", "status"],
         "family-overview", "API:/api/family/overview/"),
    ]


def _family_system_prompt(sess, today_str: str, skill_result, message: str,
                          lang: str = "zh") -> str:
    """家属会话的 system prompt（模块级，便于单测；P4 加 lang 分支）。

    名单来自登录时 ERP 返回、缓存在 session 的 residents JSON
    （[{name, building, room, relation}]）；有预取数据时只依据数据回答。
    """
    import json as _json
    try:
        residents = _json.loads(getattr(sess, "residents", "") or "[]")
    except Exception:
        residents = []
    roster = "、".join(
        f"{r.get('name', '?')}（{r.get('building') or ''}{r.get('room') or ''}，{r.get('relation') or ''}）"
        for r in residents
    ) or "（暂无绑定老人）"
    if skill_result is not None:
        data_json = _json.dumps(skill_result, ensure_ascii=False, default=str)[:8000]
        if lang == "en":
            return (
                f"You are the family-services assistant of the nursing home. Today is "
                f"{today_str}. The current user is a family member, {sess.name}. "
                f"Below is the real care data of the elder(s) bound to this family member:\n"
                f"{data_json}\n\n"
                "Answer the user's question in English based only on the data above; "
                "discuss only the elders that appear in the data, and do not make things "
                "up. The data may contain Chinese proper nouns (names, dish names) — "
                "keep them as-is. " + i18n.enum_terms_clause() + " Answer style: give "
                "the answer directly in the first "
                "sentence; answer only what was asked; do not volunteer unrequested "
                "analysis or suggestions; do not end by offering further help; expand "
                "only when the family member explicitly asks for advice. You cannot "
                "order or cancel meals or modify any data on behalf of the family; for "
                "any operation, guide them to the 'Family Services' page. Keep a warm, "
                "friendly tone. Respond in English."
            )
        return (
            f"你是养老院的家属服务助手。今天是{today_str}。当前用户是家属{sess.name}。"
            f"以下是家属绑定老人的真实照护数据：\n{data_json}\n\n"
            "请根据以上数据用中文直接回答用户问题，只谈数据中出现的老人，不要编造。"
            "回答风格：第一句话直接给出答案，只回答问到的内容，不要主动展开没问到的"
            "分析或建议，结尾不要询问是否需要进一步帮助；仅当家属明确要求建议时才展开。"
            "你不能代家属点餐、退餐或修改数据；家属需要操作时引导使用「家属服务」页面。语气温暖亲切。"
        )
    if lang == "en":
        return (
            f"You are the family-services assistant of the nursing home. Today is "
            f"{today_str}. The current user is a family member, {sess.name}. Bound "
            f"elder(s): {roster}. Only discuss the care, health, meals and fees of the "
            "bound elder(s) above. When the user asks about other elders, staff "
            "matters, or facility management, warmly and politely explain that this is "
            "beyond the scope of family services. You can only look up and relay "
            "information — you cannot order or cancel meals or modify any data on "
            "behalf of the family; for any operation, guide them to the 'Family "
            "Services' page in the top bar. Names in the data may be Chinese proper "
            "nouns — keep them as-is. " + i18n.enum_terms_clause() + " Respond in "
            "English. Keep a warm, friendly tone."
        )
    return (
        f"你是养老院的家属服务助手。今天是{today_str}。当前用户是家属{sess.name}，"
        f"绑定的老人：{roster}。请只谈论上述绑定老人的照护、健康、用餐与费用情况；"
        "用户询问其他老人、员工事务或院内管理事务时，温暖而礼貌地说明这超出家属服务范围。"
        "你只能查询和转述信息，不能代家属点餐、退餐或修改任何数据；家属需要操作时，"
        "引导使用顶栏「家属服务」页面。回答用中文，语气温暖亲切。"
    )


def _today_str(lang: str = "zh") -> str:
    """chat prompt 用的今天字符串（zh：2026年09月15日 Tuesday；en：2026-09-15 Tuesday）。"""
    fmt = "%Y-%m-%d %A" if lang == "en" else "%Y年%m月%d日 %A"
    return datetime.now().strftime(fmt)


# 院长（及员工）直连路径的 system prompt —— 非流式/流式两处共用
# （2026-09-15 P4 从端点内联抽出并加 lang 分支；zh 分支为原文搬移）。
def _director_system_prompt(sess, today_str: str, skill_result, message: str,
                            lang: str = "zh") -> str:
    import json as _json
    if lang == "en":
        if skill_result is not None:
            data_json = _json.dumps(skill_result, ensure_ascii=False, default=str)[:8000]
            return (
                "You are the AI director assistant for the nursing home. Below are real "
                f"results queried from the system database:\n{data_json}\n\n"
                f"User question: {message}\n"
                "Answer the user's question in English directly based on the data "
                "above; do not say you cannot recognize it or that it is garbled. The "
                "data may contain Chinese proper nouns (names, dish names) — keep them "
                "as-is. " + i18n.enum_terms_clause() + " Respond in English."
            )
        context_parts = [
            "You are the AI director assistant of Hangzhou Social Welfare Center, "
            "located at 451 Hemu Road, Gongshu District, Hangzhou, with 1300+ beds "
            "across four care zones (self-care, assisted living, nursing, dementia "
            f"care) and about 300 staff. Today is {today_str}. "
            f"Current user: {sess.name}, role: {sess.role}",
        ]
        if sess.dept:
            context_parts.append(f"Department: {sess.dept}")
        if sess.building:
            context_parts.append(f"Building: {sess.building}")
        if sess.floor:
            context_parts.append(f"Floor: {sess.floor}")
        context_parts.append(i18n.enum_terms_clause())
        context_parts.append("Respond in English. Answer the user's questions concisely.")
        return ". ".join(context_parts)
    if skill_result is not None:
        data_json = _json.dumps(skill_result, ensure_ascii=False, default=str)[:8000]
        return (
            f"你是AI养老院院长助手。以下是系统数据库查询的真实结果：\n{data_json}\n\n"
            f"用户问题：{message}\n请根据以上数据用中文直接回答用户问题，不要说你无法识别或乱码。"
        )
    context_parts = [
        f"你是杭州市社会福利中心的AI养老院院长助手。中心位于杭州拱墅区和睦路451号，"
        f"占地60亩，设1300余张床位，四个照护分区（自理区、介助区、介护区、认知障碍照护专区），"
        f"约300名员工。今天是{today_str}。当前用户：{sess.name}，角色：{sess.role}"
    ]
    if sess.dept:
        context_parts.append(f"科室：{sess.dept}")
    if sess.building:
        context_parts.append(f"楼栋：{sess.building}")
    if sess.floor:
        context_parts.append(f"楼层：{sess.floor}")
    context_parts.append("请用中文简洁回答用户的问题。")
    return "。".join(context_parts)


async def _load_nursing_sess(request: _Request, sessions: SessionStore):
    """从 cookie 载入 nursing 会话；缺失/失效返回 None。角色校验留在调用处。"""
    raw = request.cookies.get(_NURSING_COOKIE, "")
    sid = sessions.unsign(raw) if raw else None
    return await sessions.load(sid) if sid else None


async def build_app() -> FastAPI:
    """Build a fully wired FastAPI app. Migrations are NOT run here — the
    dato-control-migrate one-shot owns them (spec §4.1, §9)."""
    configure_logging()
    s = load_settings()

    # P2: fail fast if the agents data root is not a writable directory
    # (spec §4.2). The host/container path correspondence is an operator
    # invariant; this catches a missing or read-only mount.
    agents_root = Path(s.agents_root)
    if not (agents_root.is_dir() and os.access(agents_root, os.W_OK)):
        raise RuntimeError(
            f"agents root {agents_root} is missing or not writable — "
            "check the dato-control agents-root bind mount"
        )

    db = Database(dsn=s.db_url.get_secret_value())
    await db.connect()
    # Fail fast if the schema is missing rather than serving an empty DB.
    try:
        async with db.conn(user_id=None, role="system") as conn:
            await conn.execute("SELECT 1 FROM users LIMIT 1")
    except Exception as exc:  # noqa: BLE001
        await db.close()
        raise RuntimeError(
            "dl-control schema is missing — run the dato-control-migrate "
            "one-shot before starting the app"
        ) from exc

    # P2: sweep agents stranded in 'provisioning' by a prior crash (spec §9.4).
    from dl_control.agents.provisioning.service import reconcile_stale_provisioning

    await reconcile_stale_provisioning(db)

    # P13c+: populate workflow config cache from the DB.
    from dl_control.workflows import config_cache

    await config_cache.populate(db)

    redis = Redis.from_url(s.redis_url.get_secret_value(), decode_responses=True)
    sessions = SessionStore(
        redis=redis,
        ttl_seconds=s.session_ttl_seconds,
        secret_key=s.secret_key.get_secret_value(),
    )

    from dl_control.agents.provisioning.docker_client import DockerClient

    docker = DockerClient.from_host(s.docker_host)

    # P8: construct ProvisioningConfig and reconcile precreated agents at startup.
    from dl_control.agents.provisioning.service import ProvisioningConfig

    prov_cfg = ProvisioningConfig.from_settings(s)

    from dl_control.precreated.reconciler import reconcile_precreated

    await reconcile_precreated(db, docker=docker, cfg=prov_cfg)

    app = FastAPI(dependencies=[require_password_rotated(db=db, store=sessions)])

    # P11: recover active agents whose containers are gone/stopped (spec SS3).
    from dl_control.agents.provisioning.service import reconcile_active_agents

    app.state.active_agents_reconcile_task = asyncio.create_task(
        reconcile_active_agents(
            db,
            docker,
            prov_cfg,
            concurrency=s.reconcile_concurrency,
        )
    )

    # Coarse per-IP flood gate on the login route (spec §9). The finer
    # per-username/per-IP lockout lives inside try_login.
    def _login_rate_key(request) -> str:
        if request.url.path == "/login" and request.method == "POST":
            return request.client.host if request.client else "unknown"
        return ""

    app.add_middleware(
        RateLimitMiddleware,
        redis=redis,
        max_requests=20,
        window_seconds=s.login_rate_limit_window_seconds,
        key_fn=_login_rate_key,
        prefix="rl_login:",
    )

    from dl_control.middleware.health_signal import HealthSignalMiddleware

    app.add_middleware(HealthSignalMiddleware, db=db)

    app.mount(
        "/static",
        StaticFiles(directory=str(PACKAGE_DIR / "static")),
        name="static",
    )
    app.include_router(
        auth_routes.make_router(
            db=db,
            sessions=sessions,
            redis=redis,
            templates=TEMPLATES,
            settings=s,
        )
    )

    from dl_control import i18n_routes

    app.include_router(i18n_routes.make_router(settings=s))

    from dl_control.agents import api as agents_api
    from dl_control.auth import password_change

    app.include_router(
        password_change.make_router(
            db=db,
            sessions=sessions,
            templates=TEMPLATES,
            settings=s,
        )
    )

    app.include_router(agents_api.make_router(db=db, sessions=sessions, settings=s, docker=docker))

    from dl_control.agents import routes as agents_routes
    from dl_control.audit import routes as audit_routes
    from dl_control.dashboard import routes as dashboard_routes

    app.include_router(
        agents_routes.make_router(
            db=db,
            sessions=sessions,
            templates=TEMPLATES,
            settings=s,
        )
    )
    app.include_router(
        audit_routes.make_router(
            db=db,
            sessions=sessions,
            templates=TEMPLATES,
        )
    )
    app.include_router(
        dashboard_routes.make_router(
            db=db,
            sessions=sessions,
            templates=TEMPLATES,
            redis=redis,
        )
    )

    @app.get("/api/health")
    async def health():
        async with db.conn(user_id=None, role="system") as conn:
            await conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/")
    async def root():
        return RedirectResponse(url="/admin", status_code=302)

    # -- Nursing web UI routes (Task 4) --
    from dl_control.auth.middleware import COOKIE_NAME as _NURSING_COOKIE

    _NURSING_ROLES = frozenset(
        {"director", "nursing_dept", "logistics_dept", "building", "floor", "general"}
    )
    # 家属（role="family"）只放行对话六处门（chat 页 + 会话 CRUD + 发消息）；
    # dashboard/alerts/work-orders/reports 等员工路由仍查 _NURSING_ROLES，
    # middleware 的同款集合也不含 family —— 家属进员工页一律 302 /login。
    _CHAT_ALLOWED = _NURSING_ROLES | {"family"}

    # Helper: pick CURRENT_DATE when data exists, else the latest available
    # date from the table.  Prevents "N/A" displays when seed data is older
    # than today (fresh deploy / date drift).
    def _eff_date(table: str) -> str:
        return (
            f"COALESCE((SELECT date FROM {table} WHERE date = CURRENT_DATE LIMIT 1), "
            f"(SELECT MAX(date) FROM {table}))"
        )

    def _extract_step_summary(step_key: str, output, lang: str = "zh") -> dict | None:
        """Extract key fields from a workflow step's raw output (OpenClaw JSON
        or plain LLM response). Returns a small dict for the report UI."""
        import json as _json
        if output is None:
            return None
        if isinstance(output, str):
            try: output = _json.loads(output)
            except Exception: return {"text": output[:500]}
        if not isinstance(output, dict):
            return None

        # Unwrap OpenClaw container: {"runId":..., "result":{"payloads":[{"text":"..."}]}}
        text = None
        if "runId" in output and "result" in output:
            payloads = output.get("result", {}).get("payloads", [])
            if payloads and isinstance(payloads, list):
                # Concatenate all payload texts (the LLM can split output
                # across multiple payloads when reaching token limits).
                parts = []
                for p in payloads:
                    if isinstance(p, dict):
                        t = p.get("text", "")
                        if isinstance(t, str) and t.strip():
                            parts.append(t)
                text = "\n".join(parts) if parts else None

        # 报告类步骤（院长/总务/财务）的输出本身就是 Markdown 周报，直接展示，
        # 不再尝试解析结构化 JSON（否则会得到空的或英文 key 的字段）。
        # 工具调用旁白与正文拆开（_split_report_text），旁白前端折叠。
        if step_key in ("director-report-step", "logistics-step", "finance-step") and text:
            return _split_report_text(text)

        # Try to extract JSON from the unwrapped text (LLM often wraps JSON in ```json blocks)
        if text and isinstance(text, str):
            # Look for ```json ... ``` block first
            if "```json" in text:
                block = text.split("```json", 1)[1].split("```", 1)[0]
                try: output = _json.loads(block); text = None
                except Exception: pass
            # Fall back: last JSON line
            elif text.strip():
                for line in reversed(text.splitlines()):
                    line = line.strip()
                    if line.startswith("{") and line.endswith("}"):
                        try:
                            parsed = _json.loads(line)
                            if isinstance(parsed, dict) and len(parsed) > 1:
                                output = parsed; text = None; break
                        except Exception: pass

        # If unwrapping succeeded, dispatch per-step
        if step_key == "nursing-schedule-step":
            return {
                "building": output.get("building"),
                "staff_count": output.get("staff_count"),
                "total_shifts": output.get("total_shifts"),
                "day_shifts": output.get("day_shifts"),
                "night_shifts": output.get("night_shifts"),
                "week": output.get("week"),
                "schedule": output.get("schedule"),  # 逐日明细，报表页渲染排班表
            }
        if step_key == "logistics-step":
            return {
                "item": output.get("item"),
                "category": output.get("category"),
                "weekly_consumption": output.get("weekly_consumption"),
                "suggestion": output.get("suggestion"),
            }
        if step_key == "finance-step":
            # Flatten nested summary/cost_breakdown for card grid display
            flat = {}
            for k, v in output.items():
                if isinstance(v, dict):
                    for sk, sv in v.items():
                        flat[f"{k}.{sk}"] = sv
                elif isinstance(v, list):
                    flat[k] = str(v)
                else:
                    flat[k] = v
            return flat
        if step_key == "director-report-step":
            secs = output.get("sections", {})
            result = {
                "report_type": output.get("report_type", ""),
                "period": output.get("period", output.get("title", "")),
                "building": output.get("building", ""),
            }
            for name in ("排班概况", "物资配送", "成本预估", "重点关注"):
                if name in secs:
                    key = _REPORT_SECTIONS_EN.get(name, name) if lang == "en" else name
                    result[key] = secs[name]
            if result:
                return result
        if text:
            return {"text": text[:8000]}
        return {"text": _json.dumps(output, ensure_ascii=False)[:8000]}

    @app.get("/chat", response_class=HTMLResponse)
    async def nursing_chat(request: _Request):
        raw = request.cookies.get(_NURSING_COOKIE, "")
        sid = sessions.unsign(raw) if raw else None
        sess = await sessions.load(sid) if sid else None
        if sess is None or sess.role not in _CHAT_ALLOWED:
            return RedirectResponse(url="/login", status_code=302)
        nursing_user = {
            "user_id": sess.user_id,
            "username": sess.username,
            "name": sess.name,
            "role": sess.role,
            "dept": sess.dept,
            "building": sess.building,
            "floor": sess.floor,
        }
        return TEMPLATES.TemplateResponse(
            request,
            "nursing/chat.html",
            {"active": "chat", "nursing_user": nursing_user, "csrf_token": sess.csrf_token,
             "is_family": sess.role == "family",
             "i18n_page": _page_i18n(request, ("nursing.chat.",))},
        )

    # ── Chat history helpers ──────────────────────────────────────────
    import json as _json

    async def _get_user_chats(user_id: str) -> list[dict]:
        raw = await redis.get(f"user_chats:{user_id}")
        return _json.loads(raw) if raw else []

    async def _save_user_chats(user_id: str, chats: list[dict]):
        await redis.set(f"user_chats:{user_id}", _json.dumps(chats), ex=86400 * 30)

    async def _get_chat_msgs(chat_id: str) -> list[dict]:
        raw = await redis.get(f"chat_msgs:{chat_id}")
        return _json.loads(raw) if raw else []

    async def _save_chat_msgs(chat_id: str, msgs: list[dict]):
        await redis.set(f"chat_msgs:{chat_id}", _json.dumps(msgs), ex=86400 * 30)

    # ── Chat session list ────────────────────────────────────────────
    @app.get("/api/nursing/chats")
    async def nursing_chats_list(request: _Request):
        raw = request.cookies.get(_NURSING_COOKIE, "")
        sid = sessions.unsign(raw) if raw else None
        sess = await sessions.load(sid) if sid else None
        if sess is None or sess.role not in _CHAT_ALLOWED:
            return JSONResponse({"error": "unauthorized"}, 401)
        chats = await _get_user_chats(sess.user_id)
        return JSONResponse({"chats": chats}, 200)

    @app.post("/api/nursing/chats")
    async def nursing_chats_create(request: _Request):
        raw = request.cookies.get(_NURSING_COOKIE, "")
        sid = sessions.unsign(raw) if raw else None
        sess = await sessions.load(sid) if sid else None
        if sess is None or sess.role not in _CHAT_ALLOWED:
            return JSONResponse({"error": "unauthorized"}, 401)
        import uuid
        chat_id = str(uuid.uuid4())[:8]
        chats = await _get_user_chats(sess.user_id)
        chats.insert(0, {"id": chat_id, "title": "新对话", "created_at": __import__("time").time()})
        await _save_user_chats(sess.user_id, chats)
        return JSONResponse({"chat_id": chat_id}, 200)

    @app.get("/api/nursing/chats/{chat_id}/messages")
    async def nursing_chats_messages(chat_id: str, request: _Request):
        raw = request.cookies.get(_NURSING_COOKIE, "")
        sid = sessions.unsign(raw) if raw else None
        sess = await sessions.load(sid) if sid else None
        if sess is None or sess.role not in _CHAT_ALLOWED:
            return JSONResponse({"error": "unauthorized"}, 401)
        msgs = await _get_chat_msgs(chat_id)
        return JSONResponse({"messages": msgs}, 200)

    @app.delete("/api/nursing/chats/{chat_id}")
    async def nursing_chats_delete(chat_id: str, request: _Request):
        raw = request.cookies.get(_NURSING_COOKIE, "")
        sid = sessions.unsign(raw) if raw else None
        sess = await sessions.load(sid) if sid else None
        if sess is None or sess.role not in _CHAT_ALLOWED:
            return JSONResponse({"error": "unauthorized"}, 401)
        chats = await _get_user_chats(sess.user_id)
        chats = [c for c in chats if c["id"] != chat_id]
        await _save_user_chats(sess.user_id, chats)
        await redis.delete(f"chat_msgs:{chat_id}")
        return JSONResponse({"ok": True}, 200)

    # ── Chat send ────────────────────────────────────────────────────
    @app.post("/api/nursing/chat")
    async def nursing_chat_post(request: _Request):
        import json
        import logging
        import time
        from datetime import datetime

        import httpx
        raw = request.cookies.get(_NURSING_COOKIE, "")
        sid = sessions.unsign(raw) if raw else None
        sess = await sessions.load(sid) if sid else None
        if sess is None or sess.role not in _CHAT_ALLOWED:
            return JSONResponse({"error": "unauthorized"}, 401)

        lang = _req_lang(request)  # P4：意图/提示词语言（cookie 缺省 zh）

        try:
            body = await request.json()
        except Exception:
            raw_body = await request.body()
            body = json.loads(raw_body.decode("utf-8", errors="replace"))
        message = body.get("message", "").strip()
        original_message = message  # preserve for chat history before OCR overrides it
        image_b64 = body.get("image", "")  # optional base64 image for vision
        chat_id = body.get("chat_id", "").strip()
        if not message and not image_b64:
            return JSONResponse({"error": "empty message"}, 400)

        # Auto-create chat if no chat_id provided
        if not chat_id:
            import uuid
            chat_id = str(uuid.uuid4())[:8]
            chats = await _get_user_chats(sess.user_id)
            chats.insert(0, {"id": chat_id, "title": message[:20], "created_at": time.time()})
            await _save_user_chats(sess.user_id, chats)

        # ── File handling: OCR images before skill detection ──
        file_b64 = body.get("file", "") or image_b64
        file_name = body.get("filename", "")
        file_type = body.get("filetype", "")
        had_attachment = bool(file_b64)  # remember before OCR consumes it
        saved_image_path = ""

        if file_b64 and file_type.startswith("image/"):
            ocr_text = ""
            try:
                # Save image to disk before OCR
                import base64 as _b64
                _img_dir = "/data/agents/.ocr_uploads"
                os.makedirs(_img_dir, exist_ok=True)
                _img_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file_name or 'image.png'}"
                _img_path = os.path.join(_img_dir, _img_name)
                with open(_img_path, "wb") as _f:
                    _f.write(_b64.b64decode(file_b64))
                saved_image_path = _img_path

                ocr_url = os.environ.get("DL_OCR_URL", "http://dl-ocr:8080")
                ocr_token = os.environ.get("DL_OCR_API_TOKEN", os.environ.get("DL_INTERNAL_API_KEY", ""))
                headers = {}
                if ocr_token:
                    headers["Authorization"] = f"Bearer {ocr_token}"
                # Resize large images to avoid OCR timeout
                _ocr_image = file_b64
                if len(file_b64) > 300000:  # ~225KB raw — likely high-res
                    try:
                        import io as _io

                        from PIL import Image as _PILImage
                        _raw = _b64.b64decode(file_b64)
                        _img = _PILImage.open(_io.BytesIO(_raw))
                        if max(_img.size) > 1500:
                            _ratio = 1500 / max(_img.size)
                            _img = _img.resize((int(_img.size[0]*_ratio), int(_img.size[1]*_ratio)), _PILImage.LANCZOS)
                            _buf = _io.BytesIO()
                            _img.save(_buf, 'JPEG', quality=75)
                            _ocr_image = _b64.b64encode(_buf.getvalue()).decode()
                    except Exception:
                        pass  # keep original if resize fails
                async with httpx.AsyncClient(timeout=300.0) as ocr_client:
                    ocr_resp = await ocr_client.post(
                        f"{ocr_url}/v1/ocr",
                        json={"image": _ocr_image},
                        headers=headers,
                    )
                    if ocr_resp.status_code == 200:
                        ocr_data = ocr_resp.json()
                        ocr_text = ocr_data.get("text", "").strip()
            except Exception:
                pass

            if ocr_text:
                # Clean OCR text: collapse whitespace, remove noise
                import re as _re
                ocr_text = _re.sub(r'\n{3,}', '\n\n', ocr_text)
                ocr_text = _re.sub(r' {2,}', ' ', ocr_text)
                ocr_text = ocr_text.strip()
                user_question = message or ""
                if lang == "en":
                    message = f"The user uploaded an image. OCR result:\n\n{ocr_text[:6000]}"
                    if user_question:
                        message += f"\n\nUser question: {user_question}"
                else:
                    message = f"用户上传了一张图片，OCR 识别结果如下：\n\n{ocr_text[:6000]}"
                    if user_question:
                        message += f"\n\n用户问题：{user_question}"
            else:
                if lang == "en":
                    message = ("The user uploaded an image, but OCR could not recognize "
                               f"any text. {message or ''}")
                else:
                    message = f"用户上传了一张图片，但 OCR 未能识别出文字。{message or ''}"
            file_b64 = ""

        # ── Family branch gate ─────────────────────────────────────
        # 家属会话：跳过周报触发（nursing.ops 是员工工作流）、走 _family_skill_queries
        # 预取、不进 agent 路由（ROLE_TO_AGENT 无 "family" 键，自然落到直连 LLM）。
        is_family = sess.role == "family"

        # ── Weekly report workflow trigger (before skill intent) ──
        if not is_family and any(kw in message for kw in ("报表", "周报", "运营报表")):
            from dl_control.workflows import runs as _wfruns
            from dl_control.workflows.wake import publish_wake as _wfpw

            try:
                async with db.conn(user_id=None, role="system") as _wconn:
                    _bld = (getattr(sess, "building", None)
                            or await _default_workflow_building(_wconn))
                    _run_input = {"building": _bld}
                    await _wfruns.start_run(
                        _wconn,
                        workflow_id="nursing.ops",
                        trigger="manual",
                        run_input=_run_input,
                        actor_user_id=None,  # nursing users use text IDs (u001…), not UUIDs,
                    )
                await _wfpw(redis, reason="nursing_workflow_chat")
                return JSONResponse({
                    "reply": "已启动本周运营报表生成，正在协调护理科、总务科、财务科等 AI 助手协作。请稍后到顶部「周报」页面查看结果。",
                    "chat_id": chat_id,
                }, 200)
            except _wfruns.DuplicateActiveRunError:
                return JSONResponse({
                    "reply": "本周运营报表正在生成中，请稍后到顶部「周报」页面查看结果。",
                    "chat_id": chat_id,
                }, 200)
            except Exception as _we:
                logging.getLogger(__name__).warning(f"workflow trigger failed: {_we}")

        # ── Skill intent detection (run first) ────────────────────
        skill_result = await _collect_skill_data(
            message, sess, db, is_family, history=await _get_chat_msgs(chat_id)
        )

        # ── Agent routing (with skill data injected) ──────────────
        agent_reply = None
        ROLE_TO_AGENT = {
            "director": "director", "nursing_dept": "nursing-dept",
            "logistics_dept": "logistics-dept", "general": "general-assistant",
        }
        if sess.building and sess.building[0].isdigit():
            ROLE_TO_AGENT["building"] = f"building-{sess.building[0]}"
            ROLE_TO_AGENT["floor"] = f"building-{sess.building[0]}"
        precreated_id = ROLE_TO_AGENT.get(sess.role)
        if precreated_id:
            try:
                async with db.conn(user_id=None, role="system") as conn:
                    cur = await conn.execute("SELECT id FROM agents WHERE precreated_id = %s LIMIT 1", (precreated_id,))
                    row = await cur.fetchone()
                    if row:
                        agent_id = str(row[0])
                        env_path = f"/data/agents/{agent_id}/config/.env"
                        token = ""
                        try:
                            with open(env_path) as f:
                                for line in f:
                                    if line.startswith("DL_INTERNAL_TOKEN="):
                                        token = line.strip().split("=",1)[1].strip("'\"")
                                        break
                        except Exception: pass
                        # Inject skill data into message for Agent
                        agent_msg = message
                        if skill_result is not None:
                            data_json = json.dumps(
                                skill_result, ensure_ascii=False, default=str)[:8000]
                            agent_msg = (
                                f"系统数据库查询结果：{data_json}"
                                f"\n\n用户问题：{message}\n" + _prompt_answer_rules(lang)
                            )
                        # P4：英文会话给 agent 载荷加语言指令前缀（只影响发出去的
                        # 消息；存储历史/标题用 original_message，不受影响）；
                        # P5b 前缀并入枚举术语翻译指令
                        if lang == "en":
                            agent_msg = _agent_en_prefix() + agent_msg
                        async with httpx.AsyncClient(timeout=60.0) as client:
                            resp = await client.post(
                                f"http://dato-agent-{agent_id}:18790/dato/chat",
                                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                                json={"message": agent_msg, "session_id": f"nursing-{sess.sid[:16]}"})
                            if resp.status_code == 200:
                                data = resp.json()
                                agent_reply = data.get("reply", "") or data.get("error", "")[:500]
            except Exception: agent_reply = None
        if agent_reply:
            # Save to Redis before returning
            try:
                history = await _get_chat_msgs(chat_id)
                user_entry = {"role": "user", "content": original_message}
                if had_attachment:
                    user_entry["attachment"] = {"filename": file_name, "filetype": file_type}
                history.append(user_entry)
                history.append({"role": "assistant", "content": agent_reply})
                await _save_chat_msgs(chat_id, history[-40:])
                chats = await _get_user_chats(sess.user_id)
                for c in chats:
                    if c["id"] == chat_id and c.get("title") in ("新对话", original_message[:20]):
                        c["title"] = message[:20]
                        await _save_user_chats(sess.user_id, chats)
                        break
            except Exception: pass
            return JSONResponse({"reply": agent_reply, "chat_id": chat_id}, 200)

        # Build system prompt with skill data (direct path fallback for non-agent roles)
        today_str = _today_str(lang)
        if is_family:
            system_prompt = _family_system_prompt(sess, today_str, skill_result, message, lang)
        else:
            system_prompt = _director_system_prompt(sess, today_str, skill_result, message, lang)
        api_key = s.llm_api_key.get_secret_value()
        if not api_key:
            reply = "LLM API Key 未配置，请在 infra/.env 中设置 LLM_API_KEY"
            return JSONResponse({"reply": reply}, 200)

        # Load conversation history
        history = await _get_chat_msgs(chat_id)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history[-20:])

        if file_b64 and not file_type.startswith("image/"):
            try:
                import base64
                import io
                import re
                import zipfile
                raw = base64.b64decode(file_b64)

                # .docx = ZIP of XML files — extract text from word/document.xml
                if file_name.endswith('.docx') or file_type.endswith('officedocument'):
                    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                        xml = zf.read('word/document.xml').decode('utf-8')
                    # Extract text between <w:t> tags
                    texts = re.findall(r'<w:t[^>]*>([^<]+)</w:t>', xml)
                    file_content = ''.join(texts)[:4000]
                # Plain text files
                elif any(file_name.endswith(ext) for ext in ('.md', '.txt', '.csv', '.json', '.yaml', '.yml', '.py', '.html', '.css', '.js', '.xml', '.log')):
                    file_content = raw.decode("utf-8", errors="replace")[:4000]
                else:
                    # Binary or unknown format — try UTF-8, fall back gracefully
                    try:
                        file_content = raw.decode("utf-8")[:4000]
                    except UnicodeDecodeError:
                        message = f"用户上传了文件「{file_name}」（{file_type or '二进制'}格式），用户问题：{message or '请简述这个文件的内容'}"
                        file_content = None

                if file_content is not None:
                    message = f"用户上传了文件「{file_name}」，内容如下：\n\n{file_content}\n\n用户问题：{message or '请简述这个文件的内容'}"
            except Exception:
                message = f"用户上传了文件「{file_name}」" + (f"，用户问题：{message}" if message else "，请简述这个文件的内容")

        user_msg = {"role": "user", "content": message}
        messages.append(user_msg)

        try:
            # kimi-k2.6 only accepts temperature=1 — never send a custom temperature here.
            # 90s：推理模型在注入整周菜单/订单数据时偶发超 60s（2026-08-24 家属
            # 首问实测 42s，60s 版曾超时一次返回"AI 服务暂时不可用"）。
            # retries=1：连接层重试（含瞬时 DNS 抖动——同日容器偶发
            # "[Errno -2] Name or service not known"，复测 30/30 正常），
            # 只重连接不重请求体，不会重复扣费。
            async with httpx.AsyncClient(
                timeout=90.0,
                transport=httpx.AsyncHTTPTransport(retries=1),
            ) as client:
                payload = {
                    "model": s.llm_model,
                    "messages": messages,
                    # Reasoning model — the budget must cover reasoning tokens too.
                    "max_tokens": 2000,
                }
                # MiniMax-M3: adaptive thinking puts raw <think> text into content,
                # which this fallback displays to users verbatim — disable it.
                if "minimax" in s.llm_model.lower():
                    payload["thinking"] = {"type": "disabled"}
                resp = await client.post(
                    f"{s.llm_base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                reply = data["choices"][0]["message"]["content"]
        except Exception as exc:
            reply = f"抱歉，AI 服务暂时不可用：{str(exc)[:200]}"

        # Save to Redis — preserve original message + attachment for history display
        original_msg = body.get("message", "").strip()
        user_entry = {"role": "user", "content": original_msg}
        if file_b64:
            user_entry["attachment"] = {"filename": file_name, "filetype": file_type}
        history.append(user_entry)
        history.append({"role": "assistant", "content": reply})
        try:
            await _save_chat_msgs(chat_id, history[-40:])
            # Update chat title if first exchange
            chats = await _get_user_chats(sess.user_id)
            for c in chats:
                if c["id"] == chat_id and c.get("title") in ("新对话", message[:20]):
                    c["title"] = message[:20]
                    await _save_user_chats(sess.user_id, chats)
                    break
        except Exception:
            pass

        return JSONResponse({"reply": reply, "chat_id": chat_id}, 200)

    @app.post("/api/nursing/chat/stream")
    async def nursing_chat_stream_post(request: _Request):
        """SSE 版 /api/nursing/chat（2026-09-07）：同样的预取/路由，文字流式回传。

        agent 路径走各 agent 网关自带的 OpenAI 兼容端点 /v1/chat/completions
        （stream:true + x-openclaw-session-key，会话键沿用 receiver 时代的
        agent:main:explicit:nursing-<sid>，历史无缝续接）；家属/未路由角色走
        厂商直连 stream。事件：delta(增量) / done(终态含 chat_id) / error。
        附件（图片 OCR / 文件解析）不走本端点——前端有附件时仍调非流式端点。
        网关端点未启用或连接失败时自动回落 receiver 旧路径（一次性吐全文）。
        """
        import json
        import logging
        import time

        import httpx

        raw = request.cookies.get(_NURSING_COOKIE, "")
        sid = sessions.unsign(raw) if raw else None
        sess = await sessions.load(sid) if sid else None
        if sess is None or sess.role not in _CHAT_ALLOWED:
            return JSONResponse({"error": "unauthorized"}, 401)

        lang = _req_lang(request)  # P4：意图/提示词语言（cookie 缺省 zh）

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "bad request"}, 400)
        message = (body.get("message") or "").strip()
        chat_id = (body.get("chat_id") or "").strip()
        if not message:
            return JSONResponse({"error": "empty message"}, 400)
        if not chat_id:
            import uuid
            chat_id = str(uuid.uuid4())[:8]
            chats = await _get_user_chats(sess.user_id)
            chats.insert(0, {"id": chat_id, "title": message[:20], "created_at": time.time()})
            await _save_user_chats(sess.user_id, chats)

        is_family = sess.role == "family"

        async def _sse(obj) -> str:
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        async def _save_history(reply: str):
            try:
                history = await _get_chat_msgs(chat_id)
                history.append({"role": "user", "content": message})
                history.append({"role": "assistant", "content": reply})
                await _save_chat_msgs(chat_id, history[-40:])
                chats = await _get_user_chats(sess.user_id)
                for c in chats:
                    if c["id"] == chat_id and c.get("title") in ("新对话", message[:20]):
                        c["title"] = message[:20]
                        await _save_user_chats(sess.user_id, chats)
                        break
            except Exception:
                pass

        # ── Weekly report workflow trigger（与非流式端点同款；生成器定义在
        #    _chat_gen 之后，闭包按名字解析，触发失败回落正常问答） ──
        async def _workflow_gen():
            from dl_control.workflows import runs as _wfruns
            from dl_control.workflows.wake import publish_wake as _wfpw

            try:
                async with db.conn(user_id=None, role="system") as _wconn:
                    _bld = (getattr(sess, "building", None)
                            or await _default_workflow_building(_wconn))
                    _run_input = {"building": _bld}
                    await _wfruns.start_run(
                        _wconn, workflow_id="nursing.ops", trigger="manual",
                        run_input=_run_input, actor_user_id=None,
                    )
                await _wfpw(redis, reason="nursing_workflow_chat")
                reply = (
                    "已启动本周运营报表生成，正在协调护理科、总务科、财务科等 AI 助手协作。"
                    "请稍后到顶部「周报」页面查看结果。"
                )
            except _wfruns.DuplicateActiveRunError:
                reply = "本周运营报表正在生成中，请稍后到顶部「周报」页面查看结果。"
            except Exception:
                reply = ""  # 未触发成功则继续正常问答（见下方回落）
            if reply:
                await _save_history(reply)
                yield await _sse({"type": "delta", "content": reply})
                yield await _sse({"type": "done", "chat_id": chat_id, "reply": reply})
                return
            # 工作流触发失败 → 不吞掉消息，走正常问答
            async for chunk in _chat_gen():
                yield chunk

        # ── Skill intent detection（与非流式端点同款预取，组合问句双行注入） ──
        skill_result = await _collect_skill_data(
            message, sess, db, is_family, history=await _get_chat_msgs(chat_id)
        )

        agent_msg = message
        if skill_result is not None:
            data_json = json.dumps(skill_result, ensure_ascii=False, default=str)[:8000]
            agent_msg = (
                f"系统数据库查询结果：{data_json}\n\n用户问题：{message}\n"
                + _prompt_answer_rules(lang)
            )
        # P4：英文会话给 agent 载荷加语言指令前缀（网关 + receiver 回落共用
        # agent_msg；存储历史用 message，不受影响）；P5b 前缀并入枚举术语指令
        if lang == "en":
            agent_msg = _agent_en_prefix() + agent_msg

        # ── Agent 路由信息（同非流式端点） ──
        ROLE_TO_AGENT = {
            "director": "director", "nursing_dept": "nursing-dept",
            "logistics_dept": "logistics-dept", "general": "general-assistant",
        }
        if sess.building and sess.building[0].isdigit():
            ROLE_TO_AGENT["building"] = f"building-{sess.building[0]}"
            ROLE_TO_AGENT["floor"] = f"building-{sess.building[0]}"
        precreated_id = ROLE_TO_AGENT.get(sess.role)
        agent_id = None
        gw_token = ""
        if precreated_id:
            try:
                async with db.conn(user_id=None, role="system") as conn:
                    cur = await conn.execute(
                        "SELECT id FROM agents WHERE precreated_id = %s LIMIT 1", (precreated_id,)
                    )
                    row = await cur.fetchone()
                    if row:
                        agent_id = str(row[0])
                        # 网关 token：openclaw.json 里是明文（供应时从 ${OPENCLAW_TOKEN}
                        # 插值而来）；读不到则回落 config/.env 的 OPENCLAW_TOKEN。
                        try:
                            with open(f"/data/agents/{agent_id}/openclaw.json") as f:
                                _doc = json.load(f)
                            _auth = (_doc.get("gateway") or {}).get("auth") or {}
                            gw_token = _auth.get("token", "")
                        except Exception:
                            gw_token = ""
                        if not gw_token:
                            try:
                                with open(f"/data/agents/{agent_id}/config/.env") as f:
                                    for line in f:
                                        if line.startswith("OPENCLAW_TOKEN="):
                                            gw_token = line.strip().split("=", 1)[1].strip("'\"")
                                            break
                            except Exception:
                                pass
            except Exception:
                agent_id = None

        async def _relay_sse(lines_iter, reply_holder):
            """把上游 SSE 的 data: 行转成 delta 事件，聚合全文到 reply_holder。"""
            buf = ""
            async for raw_line in lines_iter:
                if isinstance(raw_line, bytes):
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                else:
                    line = raw_line.rstrip("\n")
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except Exception:
                        continue
                    choices = chunk.get("choices") or [{}]
                    delta = (choices[0].get("delta") or {}).get("content") or ""
                    if not delta:
                        # 非流式收尾（网关偶尔回整段 chat.completion）
                        delta = (choices[0].get("message") or {}).get("content") or ""
                    if delta:
                        buf += delta
                        yield await _sse({"type": "delta", "content": delta})
            reply_holder.append(buf)

        async def _receiver_fallback() -> str:
            """网关端点不可用时回落 receiver 旧路径（一次性全文）。"""
            token = ""
            try:
                with open(f"/data/agents/{agent_id}/config/.env") as f:
                    for line in f:
                        if line.startswith("DL_INTERNAL_TOKEN="):
                            token = line.strip().split("=", 1)[1].strip("'\"")
                            break
            except Exception:
                pass
            recv_timeout = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)
            recv_payload = {"message": agent_msg, "session_id": f"nursing-{sess.sid[:16]}"}
            async with httpx.AsyncClient(timeout=recv_timeout) as client:
                resp = await client.post(
                    f"http://dato-agent-{agent_id}:18790/dato/chat",
                    headers={"Authorization": f"Bearer {token}",
                             "Content-Type": "application/json"},
                    json=recv_payload)
                if resp.status_code == 200:
                    return resp.json().get("reply", "") or ""
            return ""

        async def _direct_llm_stream(messages):
            """厂商直连流式（家属/未路由角色）。"""
            payload = {"model": s.llm_model, "messages": messages,
                       "max_tokens": 2000, "stream": True}
            if "minimax" in s.llm_model.lower():
                payload["thinking"] = {"type": "disabled"}
            llm_timeout = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
            async with httpx.AsyncClient(timeout=llm_timeout) as client, client.stream(
                "POST",
                f"{s.llm_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {s.llm_api_key.get_secret_value()}"},
                json=payload,
            ) as resp:
                resp.raise_for_status()
                holder = []
                async for ev in _relay_sse(resp.aiter_lines(), holder):
                    yield ev
            if holder:
                yield await _sse({"type": "done", "chat_id": chat_id, "reply": holder[0]})
                await _save_history(holder[0])
                return
            yield await _sse({"type": "error", "message": "空响应"})

        async def _chat_gen():
            reply_holder = []
            try:
                if agent_id and gw_token:
                    try:
                        # agent 路径：网关 OpenAI 兼容端点，流式转发
                        sess_key = f"agent:main:explicit:nursing-{sess.sid[:16]}"
                        gw_timeout = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
                        async with httpx.AsyncClient(timeout=gw_timeout) as client, client.stream(
                            "POST",
                            f"http://dato-agent-{agent_id}:18789/v1/chat/completions",
                            headers={
                                "Authorization": f"Bearer {gw_token}",
                                "Content-Type": "application/json",
                                "x-openclaw-session-key": sess_key,
                            },
                            json={
                                "model": "openclaw/main",
                                "stream": True,
                                "messages": [{"role": "user", "content": agent_msg}],
                            },
                        ) as resp:
                            if resp.status_code != 200:
                                raise RuntimeError(f"gateway http {resp.status_code}")
                            async for ev in _relay_sse(resp.aiter_lines(), reply_holder):
                                yield ev
                    except Exception as _ge:
                        log = logging.getLogger(__name__)
                        log.warning(f"chat stream gateway failed, fallback receiver: {_ge}")
                        if not reply_holder:
                            fb = await _receiver_fallback()
                            if fb:
                                reply_holder.append(fb)
                                yield await _sse({"type": "delta", "content": fb})
                elif agent_id:
                    fb = await _receiver_fallback()
                    if fb:
                        reply_holder.append(fb)
                        yield await _sse({"type": "delta", "content": fb})

                if reply_holder:
                    reply = reply_holder[0]
                    yield await _sse({"type": "done", "chat_id": chat_id, "reply": reply})
                    await _save_history(reply)
                    return

                # ── 非 agent 角色：直连 LLM 流式（系统提示词同非流式端点） ──
                today_str = _today_str(lang)
                if is_family:
                    system_prompt = _family_system_prompt(
                        sess, today_str, skill_result, message, lang)
                else:
                    system_prompt = _director_system_prompt(
                        sess, today_str, skill_result, message, lang)
                if not s.llm_api_key.get_secret_value():
                    yield await _sse({"type": "error", "message": "LLM API Key 未配置"})
                    return
                history = await _get_chat_msgs(chat_id)
                messages = [{"role": "system", "content": system_prompt}]
                messages.extend(history[-20:])
                messages.append({"role": "user", "content": message})
                async for ev in _direct_llm_stream(messages):
                    yield ev
            except Exception as exc:
                logging.getLogger(__name__).warning(f"chat stream error: {exc}")
                msg = f"抱歉，AI 服务暂时不可用：{str(exc)[:200]}"
                yield await _sse({"type": "error", "message": msg})

        # 注：_workflow_gen 引用 _chat_gen（闭包按名字解析，此处定义已就绪）
        if not is_family and any(kw in message for kw in ("报表", "周报", "运营报表")):
            return StreamingResponse(_workflow_gen(), media_type="text/event-stream")
        return StreamingResponse(_chat_gen(), media_type="text/event-stream")

    @app.get("/nursing/test-roles", response_class=HTMLResponse)
    async def nursing_test_roles(request: _Request):
        return TEMPLATES.TemplateResponse(
            request,
            "nursing/test-roles.html",
            {
                "active": "test",
                "i18n_page": _page_i18n(
                    request, ("nursing.role.", "nursing.test.")
                ),
            },
        )

    @app.get("/dashboard", response_class=HTMLResponse)
    async def nursing_dashboard_page(request: _Request):
        raw = request.cookies.get(_NURSING_COOKIE, "")
        sid = sessions.unsign(raw) if raw else None
        sess = await sessions.load(sid) if sid else None
        if sess is None or sess.role not in _NURSING_ROLES:
            return RedirectResponse(url="/login", status_code=302)
        nursing_user = {
            "user_id": sess.user_id,
            "username": sess.username,
            "name": sess.name,
            "role": sess.role,
            "dept": sess.dept,
            "building": sess.building,
            "floor": sess.floor,
        }
        return TEMPLATES.TemplateResponse(
            request,
            "nursing/dashboard.html",
            {
                "active": "dashboard",
                "nursing_user": nursing_user,
                "csrf_token": sess.csrf_token,
                "i18n_page": _page_i18n(request, ("nursing.dashboard.",)),
            },
        )

    @app.get("/api/nursing/alerts")
    async def nursing_alerts(request: _Request):
        """告警全量列表（含已处理），/alerts 主从页数据源。

        2026-08-21 加固：原先无鉴权且经公网代理暴露（ERP 老人 PII 可被
        匿名拉取）。现在要求 nursing 会话，并按会话楼栋过滤 ERP 数据。
        2026-08-25 改版：旧版只回 pending ≤50 且丢 resident_name；现在
        全量透传（含 handled_by/handled_at），前端按 待处理/已处理 分流。
        排序：danger→warning→info，同级新上报在前。
        """
        sess = await _load_nursing_sess(request, sessions)
        if sess is None or sess.role not in _NURSING_ROLES:
            return JSONResponse({"error": "unauthorized"}, 401)
        try:
            import httpx as _hx_a
            _erp = os.environ.get("NURSING_ERP_URL", "http://192.168.10.247:9081")
            async with _hx_a.AsyncClient(timeout=10.0, headers=_erp_headers(sess)) as _cli:
                _r = await _cli.get(f"{_erp}/api/incidents/")
                if _r.status_code == 200:
                    data = _r.json()
                    items = _erp_items(data)
                    _sev = {"danger": 0, "warning": 1, "info": 2}
                    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
                    items.sort(key=lambda x: _sev.get(x.get("severity", ""), 9))
                    # P5b：en 页面枚举字段换英文（lang 取页面 i18n 口径，缺省 en）
                    _lang = i18n.normalize_lang(
                        request.cookies.get(i18n.LANG_COOKIE))
                    return {"alerts": [
                        _alert_display(i, _lang) for i in items[:100]
                    ]}
        except Exception:
            pass
        return {"alerts": []}

    @app.get("/alerts", response_class=HTMLResponse)
    async def nursing_alerts_page(request: _Request):
        raw = request.cookies.get(_NURSING_COOKIE, "")
        sid = sessions.unsign(raw) if raw else None
        sess = await sessions.load(sid) if sid else None
        if sess is None or sess.role not in _NURSING_ROLES:
            return RedirectResponse(url="/login", status_code=302)
        nursing_user = {
            "name": getattr(sess, "name", None) or sess.user_id,
            "role": sess.role,
            "dept": getattr(sess, "dept", None) or "",
            "building": getattr(sess, "building", None) or "",
            "floor": getattr(sess, "floor", None) or "",
        }
        # 2026-08-25 改版：数据改为页面 JS 拉 /api/nursing/alerts（主从
        # 筛选/详情/处理都在前端做），路由只负责会话与模板上下文。
        return TEMPLATES.TemplateResponse(request, "nursing/alerts.html", {
            "active": "alerts",
            "nursing_user": nursing_user,
            "csrf_token": sess.csrf_token,
            "i18n_page": _page_i18n(request, ("nursing.alerts.",)),
        })

    @app.post("/api/nursing/alerts/{alert_id}/handle")
    async def nursing_alerts_handle(alert_id: int, request: _Request):
        """标记已处理 — 真写路径（2026-08-25）：转发 ERP handle 端点。

        旧版只回 200 假装成功（当时 ERP 无写端点，前端样式一刷新就打回
        原形）。处理人署名取 nursing 会话姓名。
        """
        raw = request.cookies.get(_NURSING_COOKIE, "")
        sid = sessions.unsign(raw) if raw else None
        sess = await sessions.load(sid) if sid else None
        if sess is None or sess.role not in _NURSING_ROLES:
            return JSONResponse({"error": "unauthorized"}, 401)
        operator = (getattr(sess, "name", None) or sess.user_id or "")[:30]
        try:
            import httpx as _hx_h
            _erp = os.environ.get("NURSING_ERP_URL", "http://192.168.10.247:9081")
            async with _hx_h.AsyncClient(timeout=10.0, headers=_erp_headers(sess)) as _cli:
                _r = await _cli.post(
                    f"{_erp}/api/incidents/{alert_id}/handle/",
                    json={"operator": operator},
                )
            if _r.status_code == 200:
                return {"ok": True, **_r.json()}
            return JSONResponse(
                {"ok": False, "error": f"ERP {_r.status_code}: {_r.text[:200]}"}, 502
            )
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, 502)

    @app.get("/api/nursing/work-orders")
    async def nursing_work_orders_api(request: _Request):
        """工单近 30 天全量，/work-orders 主从页数据源（2026-08-25 改版）。

        旧页只服务端渲染"当天"一屏（_eff_date 兜底最近一天）；现在按日期
        全量返回，前端自分组做 日期侧栏 + 列表/详情 主从。楼长会话按
        楼栋过滤（对齐 /api/nursing/alerts 口径）。
        """
        sess = await _load_nursing_sess(request, sessions)
        if sess is None or sess.role not in _NURSING_ROLES:
            return JSONResponse({"error": "unauthorized"}, 401)
        building = (getattr(sess, "building", None) or "").strip()
        sql = (
            "SELECT w.id, w.date, w.type, w.completed, w.staff_name, w.note, "
            "r.name, r.building, r.room "
            "FROM nursing_work_orders w "
            "JOIN nursing_residents r ON w.resident_id = r.id "
            "WHERE w.date >= CURRENT_DATE - 30 "
        )
        params: tuple = ()
        if building:
            sql += "AND r.building = %s "
            params = (building,)
        sql += "ORDER BY w.date DESC, w.completed, w.id"
        try:
            async with db.conn(user_id=None, role="system") as conn:
                cur = await conn.execute(sql, params)
                rows = await cur.fetchall()
        except Exception:
            return {"orders": []}
        # P5b：en 页面工单类型换英文（DB 是中文查询词表语境，不动存储）
        _lang = i18n.normalize_lang(request.cookies.get(i18n.LANG_COOKIE))
        return {"orders": [{
            "id": r[0],
            "date": r[1].isoformat() if r[1] else "",
            "type": i18n.enum_display(r[2], _lang),
            "completed": bool(r[3]),
            "staff": r[4] or "",
            "note": r[5] or "",
            "resident": r[6] or "",
            "building": r[7] or "",
            "room": r[8] or "",
        } for r in rows]}

    @app.get("/work-orders", response_class=HTMLResponse)
    async def nursing_work_orders_page(request: _Request):
        raw = request.cookies.get(_NURSING_COOKIE, "")
        sid = sessions.unsign(raw) if raw else None
        sess = await sessions.load(sid) if sid else None
        if sess is None or sess.role not in _NURSING_ROLES:
            return RedirectResponse(url="/login", status_code=302)
        nursing_user = {
            "name": getattr(sess, "name", None) or sess.user_id,
            "role": sess.role,
            "dept": getattr(sess, "dept", None) or "",
            "building": getattr(sess, "building", None) or "",
            "floor": getattr(sess, "floor", None) or "",
        }
        # 2026-08-25 改版：数据改为页面 JS 拉 /api/nursing/work-orders
        # （日期侧栏 + 主从），此处只管会话门。
        return TEMPLATES.TemplateResponse(request, "nursing/work-orders.html", {
            "active": "dashboard",
            "nursing_user": nursing_user,
            "i18n_page": _page_i18n(request, ("nursing.orders.",)),
        })

    @app.get("/api/nursing/dashboard")
    async def nursing_dashboard_api(request: _Request):
        """Aggregated operational dashboard data for the nursing home.

        2026-08-21 加固：原先无鉴权且经公网代理暴露。现在要求 nursing
        会话（dashboard.html 同源 fetch 带 cookie，登录用户不受影响），
        ERP 调用并按会话楼栋过滤。
        """
        sess = await _load_nursing_sess(request, sessions)
        if sess is None or sess.role not in _NURSING_ROLES:
            return JSONResponse({"error": "unauthorized"}, 401)
        async with db.conn(user_id=None, role="system") as conn:
            # -- Effective date helpers: fall back to latest available data
            #    when the seed has no rows for CURRENT_DATE (fresh deploy / drift).
            _sch_d = _eff_date("nursing_schedules")
            _wo_d = _eff_date("nursing_work_orders")

            # -- Summary KPIs --
            row = await (await conn.execute(
                "SELECT count(*) FROM nursing_residents"
            )).fetchone()
            total_residents = row[0] if row else 0

            row = await (await conn.execute(
                f"SELECT count(DISTINCT staff_name) FROM nursing_schedules "
                f"WHERE date = ({_sch_d})"
            )).fetchone()
            on_duty_today = row[0] if row else 0

            inventory_alerts = 0
            try:
                import httpx as _httpx
                async with _httpx.AsyncClient(timeout=10.0, headers=_erp_headers(sess)) as _c:
                    _erp_url = os.environ.get("NURSING_ERP_URL", "http://192.168.10.247:9081")
                    _r = await _c.get(f"{_erp_url}/api/inventory/low-stock/")
                    if _r.status_code == 200:
                        inventory_alerts = len(_erp_items(_r.json()))
            except Exception:
                pass  # ERP unavailable — show 0 alerts

            # Health alerts from nursing-erp
            pending_health_alerts = 0
            try:
                import httpx as _hx3
                async with _hx3.AsyncClient(timeout=10.0, headers=_erp_headers(sess)) as _c3:
                    _erp = os.environ.get("NURSING_ERP_URL", "http://192.168.10.247:9081")
                    _r = await _c3.get(f"{_erp}/api/incidents/?handled=false")
                    if _r.status_code == 200:
                        pending_health_alerts = len(_erp_items(_r.json()))
            except Exception:
                pass

            row = await (await conn.execute(
                "SELECT count(*) FROM nursing_complaints WHERE status = 'pending'"
            )).fetchone()
            monthly_complaints = row[0] if row else 0

            # -- Yesterday comparison --
            row = await (await conn.execute(
                f"SELECT count(DISTINCT staff_name) FROM nursing_schedules "
                f"WHERE date = ({_sch_d}) - 1"
            )).fetchone()
            on_duty_yesterday = row[0] if row else 0

            inventory_alerts_yesterday = inventory_alerts  # ERP provides live data; yesterday = today snapshot

            pending_health_alerts_yesterday = pending_health_alerts  # same as above

            # -- Focus residents (from unhandled incidents via ERP) --
            focus_residents = []
            try:
                import httpx as _hx4
                async with _hx4.AsyncClient(timeout=10.0, headers=_erp_headers(sess)) as _c4:
                    _erp = os.environ.get("NURSING_ERP_URL", "http://192.168.10.247:9081")
                    _r = await _c4.get(f"{_erp}/api/incidents/?handled=false")
                    if _r.status_code == 200:
                        items = _erp_items(_r.json())
                        sev_order = {"danger": 1, "warning": 2, "info": 3}
                        items.sort(key=lambda x: sev_order.get(x.get("severity", ""), 9))
                        focus_residents = [
                            {"name": i.get("resident_name", ""),
                             "room": i.get("building", "") + (i.get("room","")[:3] if i.get("room") else ""),
                             "reason": i.get("description", ""),
                             "severity": i.get("severity", "")}
                            for i in items[:5]
                        ]
            except Exception:
                pass

            # -- Low stock items from nursing-erp --
            low_stock_items = []
            try:
                import httpx as _httpx2
                async with _httpx2.AsyncClient(timeout=10.0, headers=_erp_headers(sess)) as _c2:
                    _erp_url = os.environ.get("NURSING_ERP_URL", "http://192.168.10.247:9081")
                    _r = await _c2.get(f"{_erp_url}/api/inventory/low-stock/")
                    if _r.status_code == 200:
                        low_stock_items = [
                            {"item": i["name"], "quantity": i["quantity"],
                             "safety": i["safety_stock"], "unit": i["unit"]}
                            for i in _erp_items(_r.json())
                        ]
            except Exception:
                pass  # ERP unavailable — empty list

            # -- Schedule today --
            srows = await (await conn.execute(
                f"SELECT shift, count(DISTINCT staff_name) "
                f"FROM nursing_schedules WHERE date = ({_sch_d}) "
                f"GROUP BY shift"
            )).fetchall()
            schedule_today = {"day_shift": 0, "night_shift": 0}
            for r in srows:
                if r[0] == "白班":
                    schedule_today["day_shift"] = r[1]
                elif r[0] == "夜班":
                    schedule_today["night_shift"] = r[1]

            # -- Completion rate --
            row = await (await conn.execute(
                f"SELECT "
                f"count(*) FILTER (WHERE completed = TRUE) AS done, "
                f"count(*) AS total "
                f"FROM nursing_work_orders WHERE date = ({_wo_d})"
            )).fetchone()
            done, total = row[0] or 0, row[1] or 0
            completion_rate = f"{int(done / total * 100)}%" if total > 0 else "N/A"

            # -- Work order breakdown --
            orows = await (await conn.execute(
                f"SELECT type, count(*) as total, SUM(CASE WHEN completed THEN 1 ELSE 0 END) as done "
                f"FROM nursing_work_orders WHERE date = ({_wo_d}) "
                f"GROUP BY type ORDER BY type"
            )).fetchall()
            work_order_details = [
                {"type": r[0], "total": r[1], "done": r[2]} for r in orows
            ]

            # -- Building distribution --
            brows = await (await conn.execute(
                "SELECT building, count(*) FROM nursing_residents "
                "GROUP BY building ORDER BY building"
            )).fetchall()
            building_distribution = [
                {"building": r[0], "count": r[1]} for r in brows
            ]

            # -- 今日文娱活动（当天无数据自动取最近一天，卡面标注该日期）--
            arows = await (await conn.execute(
                "SELECT date, title, time, location FROM nursing_activities "
                f"WHERE date = ({_eff_date('nursing_activities')}) "
                "ORDER BY time NULLS LAST"
            )).fetchall()
            today_activities = _today_activities(arows)

            # -- 2026-08-25 大屏增强：ERP 五组件（单个挂掉降级为空值，不炸屏）--
            today_menu: list = []
            order_stats: dict = {}
            care_level_distribution: list = []
            assessment_review = {"pending_first_count": 0, "due_review_count": 0}
            occupancy = {"total": 0, "occupied": 0, "free": 0, "rate": None}
            try:
                import httpx as _hx5
                _erp5 = os.environ.get("NURSING_ERP_URL", "http://192.168.10.247:9081")
                async with _hx5.AsyncClient(timeout=10.0, headers=_erp_headers(sess)) as _c5:
                    # ① 今日三餐菜单：week-menu 按中文星期过滤（day 取值 周一…周日）
                    try:
                        _r = await _c5.get(f"{_erp5}/api/week-menu/",
                                           params={"week_start": _week_start()})
                        if _r.status_code == 200:
                            today_menu = _today_menu(_erp_items(_r.json()))
                    except Exception:
                        pass
                    # ② 今日点餐动态：订单分页上限 50，按餐次分三次拉（每餐 ≤36）
                    try:
                        _today = datetime.now().strftime("%Y-%m-%d")
                        per_meal = {}
                        for _mt in _MEAL_ORDER:
                            _r = await _c5.get(f"{_erp5}/api/meal-orders/",
                                               params={"date": _today, "meal_type": _mt})
                            per_meal[_mt] = _erp_items(_r.json()) if _r.status_code == 200 else []
                        order_stats = _order_stats(per_meal)
                    except Exception:
                        pass
                    # ③ 护理等级分布：residents 分页 50 ≥ 全院 36 人，单页拿全
                    try:
                        _r = await _c5.get(f"{_erp5}/api/residents/")
                        if _r.status_code == 200:
                            care_level_distribution = _care_level_pie(_erp_items(_r.json()))
                    except Exception:
                        pass
                    # ④ 评估待办（待首评 + 到期复评两计数）
                    try:
                        _r = await _c5.get(f"{_erp5}/api/assessments/review/")
                        if _r.status_code == 200:
                            _d = _r.json()
                            assessment_review = {
                                "pending_first_count": _d.get("pending_first_count", 0),
                                "due_review_count": _d.get("due_review_count", 0),
                            }
                    except Exception:
                        pass
                    # ⑤ 床位入住率（在院老人卡副行；楼长会话自动收窄到本楼）
                    try:
                        _r = await _c5.get(f"{_erp5}/api/beds/occupancy/")
                        if _r.status_code == 200:
                            occupancy = _occupancy_summary(_r.json().get("buildings", []))
                    except Exception:
                        pass
            except Exception:
                pass

        # P5b 枚举英文化（en 页面显示层）：工单类型/护理等级/餐次换英文。
        # zh 模式 enum_display 原样返回，零改动；dish name（菜名，专名）与
        # 自由文本不动。前端 MEAL_THEME 已兼容英文餐次键。
        _lang = i18n.normalize_lang(request.cookies.get(i18n.LANG_COOKIE))
        work_order_details = [
            {**w, "type": i18n.enum_display(w["type"], _lang)}
            for w in work_order_details
        ]
        care_level_distribution = [
            {**c, "name": i18n.enum_display(c["name"], _lang)}
            for c in care_level_distribution
        ]
        today_menu = [
            {**m, "meal_type": i18n.enum_display(m["meal_type"], _lang)}
            for m in today_menu
        ]
        if isinstance(order_stats, dict) and order_stats.get("meals"):
            order_stats = {
                **order_stats,
                "meals": [
                    {**m, "meal_type": i18n.enum_display(m["meal_type"], _lang)}
                    for m in order_stats["meals"]
                ],
            }

        return {
            "summary": {
                "total_residents": total_residents,
                "on_duty_today": on_duty_today,
                "inventory_alerts": inventory_alerts,
                "pending_health_alerts": pending_health_alerts,
                "monthly_complaints": monthly_complaints,
                "on_duty_yesterday": on_duty_yesterday,
            },
            "focus_residents": focus_residents,
            "low_stock_items": low_stock_items,
            "schedule_today": schedule_today,
            "completion_rate": completion_rate,
            "work_order_details": work_order_details,
            "building_distribution": building_distribution,
            "today_menu": today_menu,
            "order_stats": order_stats,
            "care_level_distribution": care_level_distribution,
            "assessment_review": assessment_review,
            "occupancy": occupancy,
            "today_activities": today_activities,
        }

    # -- Nursing workflow trigger (Task 9) --
    @app.post("/api/nursing/workflow/start")
    async def nursing_workflow_start(request: _Request, body: _NursingWorkflowStart):
        """Trigger the multi-agent nursing ops workflow.

        Requires a valid nursing session cookie. The director/dept head
        starts the chain: 护理科 → 总务科 → 财务科 → 院长报告.
        """
        raw = request.cookies.get(_NURSING_COOKIE, "")
        sid = sessions.unsign(raw) if raw else None
        sess = await sessions.load(sid) if sid else None
        if sess is None or sess.role not in _NURSING_ROLES:
            raise HTTPException(
                status_code=401,
                detail=i18n.translate(_req_lang(request), "nursing.err.login_required"),
            )
        from dl_control.workflows import runs as _wfruns

        # 楼栋解析链（P5b）：显式入参 → 会话楼栋 → 库实值兜底（en 演示期
        # nursing_schedules.building 是 "Building 3"，写死 "3号楼" 会查空）
        building = body.building or getattr(sess, "building", None)
        if not building:
            async with db.conn(user_id=None, role="system") as _bconn:
                building = await _default_workflow_building(_bconn)
        run_input: dict = {"building": building}
        if body.nursing_agent_id:
            run_input["nursing_agent_id"] = body.nursing_agent_id
        if body.logistics_agent_id:
            run_input["logistics_agent_id"] = body.logistics_agent_id
        if body.general_agent_id:
            run_input["general_agent_id"] = body.general_agent_id
        if body.director_agent_id:
            run_input["director_agent_id"] = body.director_agent_id
        try:
            async with db.conn(user_id=None, role="system") as conn:
                run_id = await _wfruns.start_run(
                    conn,
                    workflow_id="nursing.ops",
                    trigger="manual",
                    run_input=run_input,
                    actor_user_id=None,  # nursing users use text IDs (u001…), not UUIDs,
                )
        except _wfruns.UnknownWorkflowError:
            raise HTTPException(status_code=404, detail="nursing.ops workflow not found") from None
        except _wfruns.WorkflowDisabledError:
            raise HTTPException(status_code=409, detail="nursing.ops workflow is disabled") from None
        except _wfruns.DuplicateActiveRunError:
            raise HTTPException(status_code=409, detail="a nursing ops run is already active") from None
        from dl_control.workflows.wake import publish_wake as _wfpw

        await _wfpw(redis, reason="nursing_workflow_start")
        return {"run_id": str(run_id)}

    # -- Nursing weekly report (workflow results) --
    @app.get("/api/nursing/report")
    async def nursing_report_api(request: _Request, offset: int = 0, limit: int = 10):
        """周报期次列表（最新在前，分页）。

        raw 不再随列表下发（页面不渲染它，10 期 × 4 步的 OpenClaw 原文会把
        载荷撑大数倍）；审计看库里的 workflow_step.output 原值。
        """
        limit = max(1, min(limit, 50))
        offset = max(0, offset)
        async with db.conn(user_id=None, role="system") as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM workflow_run WHERE workflow_id = 'nursing.ops'"
            )
            total = (await cur.fetchone())[0]
            cur = await conn.execute(
                "SELECT id::text, status, trigger, input, "
                "created_at::timestamptz(0), finished_at::timestamptz(0) "
                "FROM workflow_run WHERE workflow_id = 'nursing.ops' "
                "ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (limit, offset),
            )
            runs_raw = await cur.fetchall()

            runs_list = []
            for r in runs_raw:
                rid = r[0]
                cur2 = await conn.execute(
                    "SELECT step_key, status, output FROM workflow_step "
                    "WHERE run_id = %s ORDER BY step_key", (rid,)
                )
                steps = {}
                for s in await cur2.fetchall():
                    summary = _extract_step_summary(s[0], s[2], _req_lang(request))
                    steps[s[0]] = {"status": s[1], "summary": summary}
                runs_list.append({
                    "id": rid, "status": r[1], "trigger": r[2],
                    "input": r[3], "created_at": str(r[4]), "finished_at": str(r[5]),
                    "steps": steps,
                })

        return {
            "runs": runs_list,
            "total": total,
            "has_more": offset + limit < total,
        }

    @app.get("/reports", response_class=HTMLResponse)
    async def nursing_reports_page(request: _Request):
        raw = request.cookies.get(_NURSING_COOKIE, "")
        sid = sessions.unsign(raw) if raw else None
        sess = await sessions.load(sid) if sid else None
        if sess is None or sess.role not in _NURSING_ROLES:
            return RedirectResponse(url="/login", status_code=302)
        nursing_user = {
            "name": getattr(sess, "name", None) or sess.user_id,
            "role": sess.role,
            "dept": getattr(sess, "dept", None) or "",
            "building": getattr(sess, "building", None) or "",
            "floor": getattr(sess, "floor", None) or "",
        }
        return TEMPLATES.TemplateResponse(request, "nursing/reports.html", {
            "active": "reports",
            "nursing_user": nursing_user,
            "csrf_token": sess.csrf_token,
            "i18n_page": _page_i18n(request, ("nursing.reports.",)),
        })

    @app.exception_handler(MustRotatePasswordError)
    async def _rotate_handler(_request, exc: MustRotatePasswordError):
        return JSONResponse(status_code=423, content={"detail": str(exc)})

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(request, exc: StarletteHTTPException):
        if exc.status_code in (302, 303):
            location = "/login"
            if exc.headers and "location" in exc.headers:
                location = exc.headers["location"]
            return StarletteRedirect(url=location, status_code=exc.status_code)
        return await http_exception_handler(request, exc)

    async def shutdown() -> None:
        await docker.close()
        await db.close()
        await redis.aclose()

    shutdown_event = asyncio.Event()

    # P4: audit mirror reconciler — drains audit_log_outbox into per-agent DBs.
    from dl_control.audit.audit_mirror import audit_mirror_loop

    mirror_lock_fd = -1
    mirror_task = None
    owner_dsn = s.owner_dsn.get_secret_value() if s.owner_dsn else None
    if owner_dsn:
        try:
            mirror_lock_path = str(agents_root / ".dato-audit-mirror.lock")
            mirror_lock_fd = os.open(mirror_lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(mirror_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            if mirror_lock_fd >= 0:
                os.close(mirror_lock_fd)
            mirror_lock_fd = -1
            logging.getLogger(__name__).warning(
                "Could not acquire audit-mirror lock; mirror task skipped"
            )
        else:
            mirror_task = asyncio.create_task(
                audit_mirror_loop(
                    db,
                    owner_dsn,
                    shutdown_event,
                    poll_seconds=s.audit_mirror_poll_seconds,
                )
            )

    # P13c: register shipped flows (spec §9) — disabled by default; the admin
    # enables them in the UI. A failure (e.g. FlowVersionConflict — a vendor
    # packaging bug) must not brick the appliance (D-P13C-11): log + continue;
    # runs pinned to a missing version already fail loudly in the runner.
    from dl_control.workflows.flows.catalog import SHIPPED_FLOWS
    from dl_control.workflows.registry import register_flows

    try:
        async with db.conn(user_id=None, role="system") as conn:
            await register_flows(conn, SHIPPED_FLOWS)
    except Exception as exc:  # noqa: BLE001
        structlog.get_logger().error("workflow_flow_registration_failed", error=str(exc))

    # P13b: workflow runner — single-writer leased loop (workflow spec §6.2).
    # Postgres is the authoritative lease; this flock only prevents a second
    # dl-control process from running a competing loop on the same box.
    from dl_control.workflows.runner import runner_loop

    workflow_lock_fd = -1
    workflow_task = None
    workflow_scheduler_task = None
    workflow_listener_task = None
    try:
        workflow_lock_path = str(agents_root / ".dato-workflow.lock")
        workflow_lock_fd = os.open(workflow_lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(workflow_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        if workflow_lock_fd >= 0:
            os.close(workflow_lock_fd)
        workflow_lock_fd = -1
        structlog.get_logger().warning("could not acquire workflow-runner lock; runner skipped")
    else:
        from dl_control.workflows.schedules import scheduler_loop
        from dl_control.workflows.wake import wake_listener

        workflow_wake_event = asyncio.Event()
        workflow_listener_task = asyncio.create_task(
            wake_listener(redis, workflow_wake_event, shutdown_event)
        )
        workflow_scheduler_task = asyncio.create_task(
            scheduler_loop(
                db,
                shutdown_event,
                tick_seconds=s.workflow_schedule_tick_seconds,
            )
        )
        from dl_control.workflows.dispatch import DispatchConfig

        workflow_dispatch_cfg = DispatchConfig(
            agents_root=s.agents_root,
            receiver_port=s.workflow_agent_receiver_port,
            http_timeout_seconds=s.workflow_agent_dispatch_timeout_seconds,
            repost_backoff_seconds=s.workflow_agent_repost_backoff_seconds,
            repost_max=s.workflow_agent_repost_max,
        )
        workflow_task = asyncio.create_task(
            runner_loop(
                db,
                shutdown_event,
                worker=f"dl-control-{os.getpid()}",
                lease_ttl_seconds=s.workflow_lease_ttl_seconds,
                poll_seconds=s.workflow_poll_seconds,
                wake_event=workflow_wake_event,
                dispatch_cfg=workflow_dispatch_cfg,
            )
        )

    # P6 — re-render Tier 1 configs if templates have changed since the
    # last boot. CURRENT_TEMPLATE_VERSION is bumped in
    # dl_control/agents/reprovision.py whenever openclaw.json.j2 changes.
    try:
        from dl_control.agents.provisioning.service import ProvisioningConfig
        from dl_control.agents.reprovision import reprovision_tier1_agents

        p6_cfg = ProvisioningConfig.from_settings(s)
        p6_summary = await reprovision_tier1_agents(
            db=db,
            docker=docker,
            cfg=p6_cfg,
            reason="startup_template_check",
        )
        structlog.get_logger().info(
            "p6_startup_reprovision",
            reprovisioned=len(p6_summary["reprovisioned"]),
            skipped=len(p6_summary["skipped"]),
            failed=len(p6_summary["failed"]),
        )
    except Exception as exc:
        structlog.get_logger().error(
            "p6_startup_reprovision_error",
            error=str(exc),
        )


    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        yield
        shutdown_event.set()
        tasks = [
            t
            for t in (
                mirror_task,
                workflow_task,
                workflow_scheduler_task,
                workflow_listener_task,
                _app.state.active_agents_reconcile_task,
            )
            if t is not None
        ]
        await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )
        if workflow_lock_fd >= 0:
            os.close(workflow_lock_fd)
        if mirror_lock_fd >= 0:
            os.close(mirror_lock_fd)
        await shutdown()

    app.router.lifespan_context = _lifespan
    app.state.shutdown = shutdown
    app.state.prov_cfg = prov_cfg
    app.state.docker = docker
    app.state.settings = s
    app.state.workflow_runner_task = workflow_task

    # P3: GBrain OAuth credential wizard
    from dl_control.agents.gbrain import routes as gbrain_creds_routes

    app.include_router(
        gbrain_creds_routes.make_router(
            db=db,
            sessions=sessions,
            settings=s,
            templates=TEMPLATES,
        )
    )

    # P5: agent token verify endpoint (internal API for dl-cognee).
    from dl_control.libraries.routes import make_library_router, make_verify_router

    app.include_router(make_verify_router(db=db, settings=s))
    app.include_router(make_library_router(db=db, sessions=sessions, settings=s))

    # P6: internal audit write path for dl-llm-proxy.
    from dl_control.audit.internal_routes import make_internal_audit_router

    app.include_router(make_internal_audit_router(db, s))

    # P6: internal llm-status endpoint for dashboard widget.
    from dl_control.llm.routes import make_llm_status_router

    app.include_router(make_llm_status_router(db, s))

    # P13c: workflow event-intake (internal) endpoint.
    from dl_control.workflows.internal_routes import make_workflow_internal_router

    app.include_router(make_workflow_internal_router(db, redis, s))

    # P13d: agent-facing workflow API — result callback + start/get (spec §7).
    from dl_control.workflows.agent_routes import make_agent_router

    app.include_router(make_agent_router(db, redis))

    # P13d+: admin internal API — Agent Manager system management (spec §8.5).
    from dl_control.agents.internal_routes import make_admin_internal_router

    app.include_router(make_admin_internal_router(db=db, docker=docker, cfg=prov_cfg, redis=redis))

    # P13c: workflow admin UI (list/detail, enable, schedules, approvals, controls).
    from dl_control.workflows import admin_routes as workflow_admin_routes

    app.include_router(
        workflow_admin_routes.make_router(
            db=db,
            sessions=sessions,
            templates=TEMPLATES,
            settings=s,
            redis=redis,
        )
    )
    return app


class LazyApp:
    """ASGI entrypoint that defers I/O until the first request."""

    def __init__(self) -> None:
        self._app: FastAPI | None = None
        self._lock = asyncio.Lock()

    async def __call__(self, scope, receive, send):
        if self._app is None:
            async with self._lock:
                if self._app is None:
                    self._app = await build_app()
        await self._app(scope, receive, send)


app = LazyApp()
