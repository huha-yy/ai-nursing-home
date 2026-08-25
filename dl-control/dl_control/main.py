"""FastAPI app factory + lazy ASGI entrypoint."""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import structlog
from dl_shared.rate_limit import RateLimitMiddleware
from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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


TEMPLATES = Jinja2Templates(
    directory=str(PACKAGE_DIR / "templates"),
    context_processors=[_i18n_context],
)


class _NursingWorkflowStart(BaseModel):
    """Request body for triggering the multi-agent nursing ops workflow."""

    building: str = "3号楼"
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
    """
    return [
        (["排班", "值班", "谁当班", "排班表"], "nursing-schedule",
         "API:/api/schedules/?date=" + datetime.now().strftime("%Y-%m-%d")),
        (["工单", "完成率", "护理完成", "任务完成"], "nursing-work-order",
         "API:/api/incidents/"),
        # 评估两行须在 logistics（"盘点"）与 resident（"老人"）行之前：
        # "评估盘点/老人的评估" 同时含对方关键词，先命中这里才有评估数据
        (["待评估", "复评", "评估盘点"], "assessment-query",
         "API:/api/assessments/review/"),
        (["评估", "定级", "能力等级", "护理等级"], "assessment-query",
         "API:/api/assessments/"),
        (["库存", "盘点", "物资", "采购", "尿不湿", "手套", "口罩", "消毒液", "胃管", "护理垫"],
         "logistics-inventory", "API:/api/inventory/"),
        (["老人", "张建国", "301", "302", "303", "108", "205", "老人档案", "健康档案"],
         "resident-query", "API:/api/residents/"),
        (["菜单", "饭菜", "今天吃什么", "伙食", "早餐", "午餐", "晚餐"], "meal-query",
         f"API:/api/week-menu/?week_start={_week_start()}"),
        (["活动", "文娱", "合唱", "讲座", "棋牌", "书法"], "activity-query",
         "SELECT title, date, time, location FROM nursing_activities "
         "WHERE date >= CURRENT_DATE ORDER BY date LIMIT 10"),
        (["欠费", "没交", "未缴", "未交", "催缴"], "finance-query",
         "API:/api/billing/arrears/"),
        (["餐费", "月结"], "finance-query", "API:/api/meal-finance/"),
        (["费用", "结算", "缴费", "账单", "应收", "出账"], "finance-query",
         "API:/api/billing/summary/"),
        (["预警", "告警", "重点关注", "异常"], "alert-query",
         "API:/api/incidents/?handled=false"),
        (["员工", "谁负责", "人员", "值班人员"], "staff-query",
         "API:/api/employees/"),
    ]


def _family_skill_queries() -> list:
    """家属会话（role="family"）专用意图映射 —— 全部走 ERP /api/family/*。

    与员工版三处刻意不同：
    - 只有 API: 行，没有 SQL 行 —— 本侧库（agents/workflows 等）没有
      家属可看的数据，SQL 预取对家属是越权面；
    - ERP 侧按 X-Family-Token 过滤，返回的永远只是绑定老人；
    - 行序即优先级（首匹配即 break）：账单/吃饭在前，泛"老人近况"兜底最后。
    """
    return [
        (["账单", "费用", "缴费", "欠费", "收费", "月结", "钱"], "family-billing",
         "API:/api/family/billing/"),
        (["吃饭", "点餐", "订餐", "退餐", "伙食", "三餐", "菜单", "饭菜", "吃什么"],
         "family-meals", "API:/api/family/meals/"),
        (["健康", "身体", "血压", "血糖", "用药", "护理", "评估", "病历", "过敏", "诊断"],
         "family-care", "API:/api/family/care/"),
        # 兜底行：泛问老人近况 → 总览（基础+评估+近期动态+今日三餐+欠费）
        (["老人", "爸", "妈", "近况", "怎么样", "状态", "情况", "照护", "住"],
         "family-overview", "API:/api/family/overview/"),
    ]


def _family_system_prompt(sess, today_str: str, skill_result, message: str) -> str:
    """家属会话的 system prompt（模块级，便于单测）。

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
        return (
            f"你是养老院的家属服务助手。今天是{today_str}。当前用户是家属{sess.name}。"
            f"以下是家属绑定老人的真实照护数据：\n{data_json}\n\n"
            "请根据以上数据用中文直接回答用户问题，只谈数据中出现的老人，不要编造。"
            "你不能代家属点餐、退餐或修改数据；家属需要操作时引导使用「家属服务」页面。语气温暖亲切。"
        )
    return (
        f"你是养老院的家属服务助手。今天是{today_str}。当前用户是家属{sess.name}，"
        f"绑定的老人：{roster}。请只谈论上述绑定老人的照护、健康、用餐与费用情况；"
        "用户询问其他老人、员工事务或院内管理事务时，温暖而礼貌地说明这超出家属服务范围。"
        "你只能查询和转述信息，不能代家属点餐、退餐或修改任何数据；家属需要操作时，"
        "引导使用顶栏「家属服务」页面。回答用中文，语气温暖亲切。"
    )


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

    def _extract_step_summary(step_key: str, output) -> dict | None:
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
        if step_key in ("director-report-step", "logistics-step", "finance-step") and text:
            return {"text": text[:8000]}

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
                    result[name] = secs[name]
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
             "is_family": sess.role == "family"},
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
        import httpx, json, time, logging, shlex
        from datetime import datetime
        raw = request.cookies.get(_NURSING_COOKIE, "")
        sid = sessions.unsign(raw) if raw else None
        sess = await sessions.load(sid) if sid else None
        if sess is None or sess.role not in _CHAT_ALLOWED:
            return JSONResponse({"error": "unauthorized"}, 401)

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
                        from PIL import Image as _PILImage
                        import io as _io
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
                message = f"用户上传了一张图片，OCR 识别结果如下：\n\n{ocr_text[:6000]}"
                if user_question:
                    message += f"\n\n用户问题：{user_question}"
            else:
                message = f"用户上传了一张图片，但 OCR 未能识别出文字。{message or ''}"
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
                _run_input = {"building": getattr(sess, "building", None) or "3号楼"}
                async with db.conn(user_id=None, role="system") as _wconn:
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
        skill_result = None
        matched_skill = None
        for keywords, skill_name, sql in (_family_skill_queries() if is_family else _skill_queries()):
            if any(kw in message for kw in keywords):
                matched_skill = skill_name
                try:
                    if sql.startswith("API:"):
                        # Query nursing-erp API instead of database
                        import httpx as _hx
                        _erp = os.environ.get("NURSING_ERP_URL", "http://192.168.10.247:9081")
                        _path = sql[4:]  # strip "API:"
                        async with _hx.AsyncClient(timeout=10.0, headers=_erp_headers(sess)) as _cli:
                            _resp = await _cli.get(f"{_erp}{_path}")
                            if _resp.status_code == 200:
                                skill_result = _erp_items(_resp.json())[:50]
                            else:
                                skill_result = None
                    else:
                        async with db.conn(user_id=None, role="system") as conn:
                            cur = await conn.execute(sql)
                            rows = await cur.fetchall()
                            cols = [d[0] for d in cur.description]
                            skill_result = [dict(zip(cols, r)) for r in rows][:50]
                except Exception as _e:
                    import logging
                    logging.getLogger(__name__).warning(f"Skill {skill_name} query failed: {_e}")
                    skill_result = None
                break

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
                            agent_msg = f"系统数据库查询结果：{json.dumps(skill_result, ensure_ascii=False, default=str)[:8000]}\n\n用户问题：{message}\n请根据以上真实数据回答，不要编造。"
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
        today_str = datetime.now().strftime("%Y年%m月%d日 %A")
        if is_family:
            system_prompt = _family_system_prompt(sess, today_str, skill_result, message)
        else:
            context_parts = [f"你是杭州市社会福利中心的AI养老院院长助手。中心位于杭州拱墅区和睦路451号，占地60亩，设1300余张床位，四个照护分区（自理区、介助区、介护区、认知障碍照护专区），约300名员工。今天是{today_str}。当前用户：{sess.name}，角色：{sess.role}"]
            if sess.dept: context_parts.append(f"科室：{sess.dept}")
            if sess.building: context_parts.append(f"楼栋：{sess.building}")
            if sess.floor: context_parts.append(f"楼层：{sess.floor}")
            context_parts.append("请用中文简洁回答用户的问题。")
            system_prompt = "。".join(context_parts)
            if skill_result is not None:
                data_json = json.dumps(skill_result, ensure_ascii=False, default=str)[:8000]
                system_prompt = f"你是AI养老院院长助手。以下是系统数据库查询的真实结果：\n{data_json}\n\n用户问题：{message}\n请根据以上数据用中文直接回答用户问题，不要说你无法识别或乱码。"
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
                import base64, io, zipfile, re
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
                resp = await client.post(
                    f"{s.llm_base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": s.llm_model,
                        "messages": messages,
                        # Reasoning model — the budget must cover reasoning tokens too.
                        "max_tokens": 2000,
                    },
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

    @app.get("/nursing/test-roles", response_class=HTMLResponse)
    async def nursing_test_roles(request: _Request):
        return TEMPLATES.TemplateResponse(
            request,
            "nursing/test-roles.html",
            {"active": "test"},
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
            {"active": "dashboard", "nursing_user": nursing_user, "csrf_token": sess.csrf_token},
        )

    @app.get("/api/nursing/alerts")
    async def nursing_alerts(request: _Request):
        """Return pending (unhandled) health alerts from nursing-erp.

        2026-08-21 加固：原先无鉴权且经公网代理暴露（ERP 老人 PII 可被
        匿名拉取）。现在要求 nursing 会话，并按会话楼栋过滤 ERP 数据。
        """
        sess = await _load_nursing_sess(request, sessions)
        if sess is None or sess.role not in _NURSING_ROLES:
            return JSONResponse({"error": "unauthorized"}, 401)
        try:
            import httpx as _hx_a
            _erp = os.environ.get("NURSING_ERP_URL", "http://192.168.10.247:9081")
            async with _hx_a.AsyncClient(timeout=10.0, headers=_erp_headers(sess)) as _cli:
                _r = await _cli.get(f"{_erp}/api/incidents/?handled=false")
                if _r.status_code == 200:
                    data = _r.json()
                    items = _erp_items(data)
                    return [
                        {"id": i["id"], "resident_id": i.get("resident_id", 0),
                         "content": i.get("description", ""),
                         "category": i.get("category_display", i.get("category", "")),
                         "severity": i.get("severity", ""),
                         "created_at": i.get("created_at", ""),
                         "handled": False}
                        for i in items[:50]
                    ]
        except Exception:
            pass
        return []

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
        # Fetch alerts from nursing-erp API
        alerts = []
        try:
            import httpx as _hx2
            _erp = os.environ.get("NURSING_ERP_URL", "http://192.168.10.247:9081")
            async with _hx2.AsyncClient(timeout=10.0, headers=_erp_headers(sess)) as _cli2:
                _r = await _cli2.get(f"{_erp}/api/incidents/")
                if _r.status_code == 200:
                    data = _r.json()
                    items = _erp_items(data)
                    severity_order = {"danger": 1, "warning": 2, "info": 3}
                    items.sort(key=lambda x: severity_order.get(x.get("severity", ""), 9))
                    alerts = [{
                        "id": i["id"],
                        "name": i.get("resident_name", ""),
                        "room": i.get("building", "") + i.get("room", "").replace(i.get("building", ""), "") if i.get("building") else "",
                        "content": i.get("description", ""),
                        "category": i.get("category_display", i.get("category", "")),
                        "severity": i.get("severity", ""),
                        "created_at": i.get("created_at", "")[:19] if i.get("created_at") else "",
                        "handled": i.get("handled", False),
                    } for i in items]
        except Exception:
            pass
        return TEMPLATES.TemplateResponse(request, "nursing/alerts.html", {
            "active": "alerts",
            "nursing_user": nursing_user,
            "csrf_token": sess.csrf_token,
            "alerts": alerts,
        })

    @app.post("/api/nursing/alerts/{alert_id}/handle")
    async def nursing_alerts_handle(alert_id: int, request: _Request):
        raw = request.cookies.get(_NURSING_COOKIE, "")
        sid = sessions.unsign(raw) if raw else None
        sess = await sessions.load(sid) if sid else None
        if sess is None or sess.role not in _NURSING_ROLES:
            return JSONResponse({"error": "unauthorized"}, 401)
        # Mark as handled in nursing-erp — no equivalent write endpoint yet,
        # so just acknowledge. The ERP Admin can mark incidents as handled.
        return JSONResponse({"status": "ok", "message": "请在 ERP 管理后台标记为已处理"}, 200)

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
        async with db.conn(user_id=None, role="system") as conn:
            cur = await conn.execute(
                f"SELECT w.type, r.room, r.name, w.completed, w.date, "
                f"w.staff_name, w.note "
                f"FROM nursing_work_orders w "
                f"JOIN nursing_residents r ON w.resident_id = r.id "
                f"WHERE w.date = ({_eff_date('nursing_work_orders')}) "
                f"ORDER BY w.completed, w.type"
            )
            rows = await cur.fetchall()
        orders = [{
            "type": r[0], "room": r[1], "resident": r[2],
            "done": r[3],
            "time": r[4].strftime("%m月%d日") if r[4] else "",
            "staff": r[5] or "", "note": r[6] or "",
        } for r in rows]
        return TEMPLATES.TemplateResponse(request, "nursing/work-orders.html", {
            "active": "dashboard",
            "nursing_user": nursing_user,
            "orders": orders,
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
            raise HTTPException(status_code=401, detail="需要护理系统登录")
        from dl_control.workflows import runs as _wfruns

        run_input: dict = {"building": body.building}
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
    async def nursing_report_api():
        """Return the latest nursing-ops workflow run with extracted step data."""
        async with db.conn(user_id=None, role="system") as conn:
            cur = await conn.execute(
                "SELECT id::text, status, trigger, input, "
                "created_at::timestamptz(0), finished_at::timestamptz(0) "
                "FROM workflow_run WHERE workflow_id = 'nursing.ops' "
                "ORDER BY created_at DESC LIMIT 10"
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
                    out = s[2]
                    summary = _extract_step_summary(s[0], out)
                    steps[s[0]] = {"status": s[1], "summary": summary, "raw": out}
                runs_list.append({
                    "id": rid, "status": r[1], "trigger": r[2],
                    "input": r[3], "created_at": str(r[4]), "finished_at": str(r[5]),
                    "steps": steps,
                })

        return {"runs": runs_list}

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
