"""P5b 枚举英文化（2026-09-15 广交会英文演示）单测。

覆盖：
- ENUM_ZH_EN 词典：术语分组键全部在词典里（prompt 词表不悬空）
- enum_display：zh 原样 / en 整值映射 / 未知值原样 / 非 str 原样
- 整值匹配钉：「男」能换 Male，含"男"的人名不受误伤（禁子串替换）
- _alert_display：/alerts 页载荷 en 时 category/severity_display 换英文，
  name/content 原样；zh 时零改动
- chat 英文 prompt 术语指令：en 版 director/family/agent 前缀含术语翻译
  句（与词典同源生成），zh 版不含
"""

from __future__ import annotations

from dl_control import i18n
from dl_control.main import (
    _agent_en_prefix,
    _alert_display,
    _director_system_prompt,
    _family_system_prompt,
)

# ---- 词典完整性 ----


def test_enum_term_groups_subset_of_dict():
    """prompt 术语分组的每个键都必须在 ENUM_ZH_EN 里（否则生成的指令自相矛盾）"""
    for keys in i18n.ENUM_TERM_GROUPS.values():
        for k in keys:
            assert k in i18n.ENUM_ZH_EN, k


def test_enum_dict_covers_known_page_enums():
    """页面实测漏点的最小覆盖钉：告警类型/餐次/护理等级/性别/班次/严重度"""
    for k in (
        "摔倒",
        "突发不适",
        "情绪异常",
        "早餐",
        "午餐",
        "晚餐",
        "自理",
        "半护",
        "全护",
        "失智",
        "男",
        "女",
        "白班",
        "夜班",
        "危急",
        "紧急",
        "一般",
    ):
        assert k in i18n.ENUM_ZH_EN, k


# ---- enum_display ----


def test_enum_display_zh_passthrough():
    """zh 模式零改动：中文值原样返回"""
    assert i18n.enum_display("摔倒", "zh") == "摔倒"
    assert i18n.enum_display("男", "zh") == "男"
    assert i18n.enum_display("Breakfast", "zh") == "Breakfast"


def test_enum_display_en_maps():
    """en 模式整值命中词典换英文"""
    assert i18n.enum_display("摔倒", "en") == "Fall"
    assert i18n.enum_display("突发不适", "en") == "Sudden Discomfort"
    assert i18n.enum_display("情绪异常", "en") == "Mood Disturbance"
    assert i18n.enum_display("早餐", "en") == "Breakfast"
    assert i18n.enum_display("自理", "en") == "Self-care"
    assert i18n.enum_display("半护", "en") == "Partial care"
    assert i18n.enum_display("全护", "en") == "Full care"
    assert i18n.enum_display("失智", "en") == "Dementia care"
    assert i18n.enum_display("男", "en") == "Male"
    assert i18n.enum_display("女", "en") == "Female"
    assert i18n.enum_display("白班", "en") == "Day Shift"
    assert i18n.enum_display("夜班", "en") == "Night Shift"
    # 工单类型（DB 是中文查询词表语境；en overlay 数据已是英文则查不到，原样）
    assert i18n.enum_display("血压测量", "en") == "BP Measurement"


def test_enum_display_unknown_and_non_str():
    """未知值 / 非 str 原样返回（人名、菜名、英文 overlay 值不受影响）"""
    assert i18n.enum_display("张国栋", "en") == "张国栋"
    assert i18n.enum_display("BP Measurement", "en") == "BP Measurement"
    assert i18n.enum_display("", "en") == ""
    assert i18n.enum_display(None, "en") is None
    assert i18n.enum_display(3, "en") == 3


def test_enum_display_whole_value_match_only():
    """整值匹配钉：单字「男/女」不做子串替换，含该字的人名/值不受误伤"""
    assert i18n.enum_display("王男", "en") == "王男"
    assert i18n.enum_display("男士护理", "en") == "男士护理"
    assert i18n.enum_display("男", "en") == "Male"


# ---- /alerts 载荷（ERP 行 → 页面显示行）----


def _erp_incident_row() -> dict:
    """ERP /api/incidents/ 典型行（zh-hans 默认语言下的 display 字段）"""
    return {
        "id": 7,
        "resident_name": "张国栋",
        "building": "Building 3",
        "category": "fall",
        "category_display": "摔倒",
        "severity": "danger",
        "severity_display": "危急",
        "description": "午饭后在走廊摔倒",
        "handled": False,
        "handled_by": "",
        "handled_at": None,
        "created_at": "2026-09-15T12:00:00",
    }


def test_alert_display_en():
    row = _alert_display(_erp_incident_row(), "en")
    assert row["category"] == "Fall"
    assert row["severity_display"] == "Critical"
    # 非枚举字段不动：人名/楼栋/自由文本描述
    assert row["name"] == "张国栋"
    assert row["building"] == "Building 3"
    assert row["content"] == "午饭后在走廊摔倒"
    assert row["severity"] == "danger"  # 机器码（前端 CSS 类/分流用）不翻


def test_alert_display_zh_unchanged():
    src = _erp_incident_row()
    row = _alert_display(src, "zh")
    assert row["category"] == "摔倒"
    assert row["severity_display"] == "危急"
    assert row == _alert_display(src, "zh")


# ---- chat 英文 prompt 术语指令 ----


class _Sess:
    def __init__(self, name="王建国", role="director", residents=None):
        self.name = name
        self.role = role
        self.dept = None
        self.building = None
        self.floor = None
        self.residents = residents


def test_enum_terms_clause_generated_from_dict():
    """指令从词典生成：五组术语的 zh/en 两侧都出现，措辞收尾在专名保留句"""
    clause = i18n.enum_terms_clause()
    assert clause.startswith("When the data mentions Chinese enum terms")
    for token in (
        "Self-care",
        "Breakfast",
        "Male",
        "Fall",
        "Day Shift",
        "自理",
        "早餐",
        "男",
        "摔倒",
        "白班",
    ):
        assert token in clause, token
    assert "keep person names, dish names and place names in Chinese as-is" in clause


def test_director_prompt_en_contains_enum_terms():
    sess = _Sess()
    en_data = _director_system_prompt(
        sess, "2026-09-15 Tuesday", [{"care_level": "自理"}], "who?", "en"
    )
    assert "Self-care" in en_data
    en_nodata = _director_system_prompt(sess, "2026-09-15 Tuesday", None, "hi", "en")
    assert "Self-care" in en_nodata
    assert "Day Shift" in en_nodata


def test_family_prompt_en_contains_enum_terms():
    sess = _Sess(name="王丽华", role="family", residents="[]")
    en_data = _family_system_prompt(
        sess, "2026-09-15 Tuesday", [{"meal_type": "早餐"}], "breakfast?", "en"
    )
    assert "Breakfast" in en_data
    en_nodata = _family_system_prompt(sess, "2026-09-15 Tuesday", None, "hi", "en")
    assert "Male" in en_nodata


def test_agent_en_prefix_contains_enum_terms():
    prefix = _agent_en_prefix()
    assert prefix.startswith("[Please respond in English.]")
    assert "Fall" in prefix
    assert "白班" in prefix


def test_prompts_zh_without_enum_terms_english():
    """zh 分支不夹带英文术语指令（中文行为零改动）"""
    sess = _Sess()
    zh = _director_system_prompt(sess, "2026年09月15日 Tuesday", None, "你好")
    zh_data = _director_system_prompt(sess, "2026年09月15日 Tuesday", [{"a": 1}], "你好")
    fam_zh = _family_system_prompt(
        _Sess(name="王丽华", residents="[]"), "2026年09月15日", None, "近况"
    )
    for p in (zh, zh_data, fam_zh):
        assert "Self-care" not in p
        assert "When the data mentions Chinese enum terms" not in p
