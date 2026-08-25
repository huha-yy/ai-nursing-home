"""周报正文/旁白拆分（_split_report_text）单测（2026-08-25）。

背景：OpenClaw 把 agent 工具调用旁白（"📋 已读取技能说明… / ⚠️ 缺少
asyncpg 依赖…"）与最终报告拼在同一串 payload 文本里，/reports 页曾把
旁白当正文一起渲染。拆分口径：正文 = 首个 markdown 标题行（#/##/###）起；
标题前旁白 + 报告尾部混入的"执行摘要：…"归 process（前端折叠，raw 仍
全量留档）。summary 在 /api/nursing/report 读取时现算，改函数即可救回
历史记录。
"""

from dl_control import main as m


def test_split_narration_and_report():
    """旁白在前 + 报告 + 尾部执行摘要 → 三段各归其位"""
    text = (
        "📋 已读取技能说明。\n⚠️ 缺少 asyncpg 依赖，自动修复。\n"
        "# 🏥 运营周报\n## 一、排班\n- 白班 14\n"
        "执行摘要：过程中自动修复 1 个错误。"
    )
    out = m._split_report_text(text)
    assert out["text"] == "# 🏥 运营周报\n## 一、排班\n- 白班 14"
    assert "📋" in out["process"] and "执行摘要" in out["process"]
    assert "# 🏥" not in out["process"]


def test_split_no_heading_falls_back_to_whole_text():
    """无 markdown 标题（纯文本输出）→ 原样整体当正文，不拆"""
    out = m._split_report_text("纯文本输出，没有任何标题行")
    assert out["text"] == "纯文本输出，没有任何标题行"
    assert out["process"] is None


def test_split_report_only_no_process():
    """正文之前没有任何旁白 → process 为 None 而不是空串"""
    out = m._split_report_text("## 报告正文")
    assert out["text"] == "## 报告正文"
    assert out["process"] is None
