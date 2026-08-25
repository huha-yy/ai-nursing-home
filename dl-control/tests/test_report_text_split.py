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


def test_strip_machine_handoff_json_section():
    """尾部"六、输出 JSON（供下一部门使用）"是 agent 间交接段 → 展示中整体剥除"""
    text = (
        "# 💰 成本预估报告\n\n## 一、概况\n内容。\n\n"
        "## 六、输出 JSON（供下一部门使用）\n```json\n{\"total\": 12328}\n```"
    )
    out = m._split_report_text(text)
    assert out["text"] == "# 💰 成本预估报告\n\n## 一、概况\n内容。"
    assert "供下一部门" not in out["text"]
    assert out["process"] is None  # 交接段不进旁白（raw 已留档）


def test_strip_bare_trailing_json_block():
    """无"输出 JSON"标题、只有收尾 ```json 块 → 也剥（且不动前半正文）"""
    text = "旁白一句。\n\n# 报告\n\n概况内容。\n\n```json\n{\"a\": 1}\n```"
    out = m._split_report_text(text)
    assert out["text"].endswith("概况内容。")
    assert "```json" not in out["text"]
    assert out["process"] == "旁白一句。"


def test_strip_json_block_with_short_outro():
    """裸 ```json 块后跟一句收尾话（总务科 8-14 实际形态）→ 连收尾话一起剥"""
    text = (
        "# 📦 物资配送计划\n\n## 三、执行建议\n1. 按计划配送\n\n"
        "```json\n{\"runId\": \"a3d5\", \"daily_delivery\": {}}\n```\n\n"
        "以上结果可直接作为下一部门（采购/配送执行）的输入。"
    )
    out = m._split_report_text(text)
    assert out["text"].endswith("1. 按计划配送")
    assert "runId" not in out["text"]
    assert "下一部门" not in out["text"]


def test_mid_text_json_block_kept():
    """正文中部引用的 json 块（非收尾交接）不误伤"""
    text = "# 报告\n\n示例配置：\n```json\n{\"a\": 1}\n```\n\n后文还有一大段结论内容。" + "结论。" * 50
    out = m._split_report_text(text)
    assert "```json" in out["text"]
