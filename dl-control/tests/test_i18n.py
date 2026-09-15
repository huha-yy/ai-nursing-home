"""i18n 目录钉子 + 双语模板渲染冒烟（2026-09-15 第一批双语化）。

- test_catalog_key_parity: MESSAGES en/zh key 集合必须一致（i18n.py 文档
  钦点的钉子，此前缺失，本批补上）
- 模板渲染冒烟：不起完整栈，直接用 main.TEMPLATES 渲染 admin/nursing 两批
  模板（stub context），zh/en 各渲一遍——语法错误、缺 key、未注册过滤器
  （|tojson）都会在这里炸
"""

from __future__ import annotations

from types import SimpleNamespace

from dl_control import i18n
from dl_control.main import TEMPLATES

# ---- catalog parity ----


def test_catalog_key_parity():
    """en/zh 必须携带完全相同的 key 集合。"""
    assert set(i18n.MESSAGES["en"]) == set(i18n.MESSAGES["zh"])


def test_catalog_keys_nonempty_strings():
    """所有值必须是 str（空串仅允许出现在刻意的单位后缀 key 里）。"""
    allowed_empty = {
        "admin.dashboard.more_suffix",
        "admin.audit.records_unit",
        "nursing.dashboard.js.person_unit",
    }
    for lang in i18n.LANGS:
        for key, value in i18n.MESSAGES[lang].items():
            assert isinstance(value, str), f"{lang}.{key} 不是 str"
            assert value or key in allowed_empty, f"{lang}.{key} 意外为空"


def test_translate_missing_key_returns_key():
    assert i18n.translate("en", "no.such.key") == "no.such.key"


def test_dump_prefixed_strips_prefix():
    blob = i18n.dump_prefixed("zh", ("nursing.dashboard.js.",))
    import json

    data = json.loads(blob)
    assert data["loading"] == "加载中…"
    assert data["locale"] == "zh-CN"
    assert not any(k.startswith("nursing.") for k in data)


# ---- 模板渲染冒烟（zh + en）----


def _ctx(lang: str, extra: dict | None = None) -> dict:
    ctx = {
        "lang": lang,
        "html_lang": i18n.HTML_LANG[lang],
        "t": i18n.translator(lang),
        "csrf_token": "tok",
        "current_user": None,
        "nursing_user": None,
        "active": "",
    }
    ctx.update(extra or {})
    return ctx


def _nursing_ctx(lang: str, page_prefix: str, extra: dict | None = None) -> dict:
    user = {"role": "director", "name": "王建国"}
    return _ctx(lang, {
        "nursing_user": user,
        "i18n_page": i18n.dump_prefixed(lang, (page_prefix,)),
        **(extra or {}),
    })


def _agent_ns() -> SimpleNamespace:
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        display_name="院长",
        precreated_id="director",
        tier="tier0",
        status="active",
        model_selection="kimi",
        created_at="2026-09-01",
        updated_at="2026-09-01",
        channel_config="{}",
        skill_list=["meal-query"],
        precreated_source_drift=False,
        precreated_source_removed=False,
        precreated_current_sha="abc",
    )


def _flow_ns() -> SimpleNamespace:
    return SimpleNamespace(
        id="nursing.ops",
        display_name="护理运营周报",
        latest_version="1.0.0",
        enabled=True,
        description="desc",
        default_agent_id=None,
        default_agent_name=None,
    )


def _run_ns() -> SimpleNamespace:
    return SimpleNamespace(
        id="0123456789abcdef",
        workflow_id="nursing.ops",
        status="succeeded",
        trigger="api",
        correlation_key="weekly",
        workflow_version="1.0.0",
        created_at="2026-09-14 09:00",
        finished_at="2026-09-14 09:05",
    )


# (模板路径, 上下文工厂)
_TEMPLATES_UNDER_TEST: list[tuple[str, object]] = [
    ("admin/dashboard.html", lambda: {"total_agents": 3, "tier0": 2, "tier1": 1,
                                      "events_today": 5, "events_week": 20}),
    ("admin/audit_list.html", lambda: {"events": [], "page": 1, "has_next": False,
                                       "events_today": 5, "events_week": 20}),
    ("admin/agents_list.html", lambda: {"agents": [], "vendor_skills": [], "custom_skills": []}),
    ("admin/agent_detail.html", lambda: {"agent": _agent_ns(), "vendor_skills": [],
                                         "custom_skills": []}),
    ("admin/workflows/list.html", lambda: {"approvals": [], "flows": [], "recent": []}),
    ("admin/workflows/detail.html", lambda: {"flow": _flow_ns(), "schedules": [], "grants": [],
                                             "grantable": [], "active_agents": [], "runs": []}),
    ("admin/workflows/run_detail.html", lambda: {"run": _run_ns(), "pending_approvals": [],
                                                 "unresolved": [], "steps": [], "ledger": []}),
]


def test_admin_templates_render_zh_and_en():
    for tpl, ctx_factory in _TEMPLATES_UNDER_TEST:
        for lang in ("zh", "en"):
            html = TEMPLATES.get_template(tpl).render(
                _ctx(lang, {**ctx_factory(), "current_user": True})
            )
            assert html.strip(), f"{tpl} ({lang}) 渲染为空"


def test_nursing_templates_render_zh_and_en():
    pages = [
        ("nursing/dashboard.html", "nursing.dashboard.", {}),
        ("nursing/reports.html", "nursing.reports.", {}),
        ("nursing/work-orders.html", "nursing.orders.", {}),
        ("nursing/alerts.html", "nursing.alerts.", {}),
        ("nursing/chat.html", "nursing.chat.", {}),
        ("nursing/test-roles.html", "nursing.role.", {}),
    ]
    # test-roles 页 JS 还引用 nursing.test.* 段，前缀不同，单造
    for tpl, prefix, extra in pages:
        prefixes = (prefix,) if tpl != "nursing/test-roles.html" else ("nursing.role.", "nursing.test.")
        for lang in ("zh", "en"):
            html = TEMPLATES.get_template(tpl).render(
                _nursing_ctx(lang, prefixes[0], {**extra, "i18n_page": i18n.dump_prefixed(lang, prefixes)})
            )
            assert html.strip(), f"{tpl} ({lang}) 渲染为空"


def test_nursing_dashboard_bilingual_titles():
    """zh cookie 下含中文标题，en cookie 下含英文标题（冒烟口径）。"""
    zh_html = TEMPLATES.get_template("nursing/dashboard.html").render(
        _nursing_ctx("zh", "nursing.dashboard.")
    )
    en_html = TEMPLATES.get_template("nursing/dashboard.html").render(
        _nursing_ctx("en", "nursing.dashboard.")
    )
    assert "运营总览" in zh_html
    assert "Operations Overview" in en_html
    # base.html 顶栏双语 + 语言切换按钮
    assert "AI 养老院院长" in zh_html
    assert "AI Nursing Home Director" in en_html
    assert "/lang/en" in zh_html and "/lang/zh" in en_html


def test_nursing_chat_bilingual_titles():
    """chat 页 zh/en 冒烟：侧栏标题、placeholder 双语；快捷查询 prompt 保持中文。"""
    zh_html = TEMPLATES.get_template("nursing/chat.html").render(
        _nursing_ctx("zh", "nursing.chat.")
    )
    en_html = TEMPLATES.get_template("nursing/chat.html").render(
        _nursing_ctx("en", "nursing.chat.")
    )
    # 侧栏 + 输入框 placeholder
    assert "对话记录" in zh_html
    assert "Conversations" in en_html
    assert "输入您的问题" in zh_html
    assert 'placeholder="Type your question' in en_html
    # 发送按钮 / 欢迎语
    assert "发送" in zh_html
    assert ">Send</button>" in en_html
    assert "您好，王建国！" in zh_html
    assert "Hello, 王建国!" in en_html
    # 快捷查询 prompt 是发给后端的中文语义，不随界面语言切换（P4 另做）
    assert "quickAsk('全院在院老人总数')" in en_html
    # 家属角色分支：en 下欢迎语走 family 文案
    fam_html = TEMPLATES.get_template("nursing/chat.html").render(
        _nursing_ctx("en", "nursing.chat.", {"nursing_user": {"role": "family", "name": "李家属"}})
    )
    assert "AI Family Assistant" in fam_html


def test_admin_dashboard_bilingual_titles():
    zh_html = TEMPLATES.get_template("admin/dashboard.html").render(
        _ctx("zh", {"total_agents": 3, "tier0": 2, "tier1": 1,
                    "events_today": 5, "events_week": 20, "current_user": True})
    )
    en_html = TEMPLATES.get_template("admin/dashboard.html").render(
        _ctx("en", {"total_agents": 3, "tier0": 2, "tier1": 1,
                    "events_today": 5, "events_week": 20, "current_user": True})
    )
    assert "系统概览" in zh_html
    assert "System overview" in en_html


def test_lang_toggle_in_nursing_topbar():
    html = TEMPLATES.get_template("nursing/alerts.html").render(
        _nursing_ctx("zh", "nursing.alerts.")
    )
    assert 'class="lang-toggle"' in html
