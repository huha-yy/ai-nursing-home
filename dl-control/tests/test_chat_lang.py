"""chat 英文语义层（P4，2026-09-15）单测 —— 不跑真 LLM。

覆盖：
- _req_lang：lang cookie 归一，缺省/脏值回落 zh（对话行为默认中文）
- 意图关键词英文扩展：英文问句逐个命中正确意图行（_match_skill_rows +
  员工/家属两张表），且命中行数与中文等价问句一致
- 中文回归：原有关键词问句命中不变
- system prompt 语言分支：en 含 "Respond in English"/机构英文名，zh 含"用中文"
- _schedule_window 英文时间词：tomorrow/day after tomorrow/next 3 days/
  this week/last 3 days 照对应中文分支语义
"""

from __future__ import annotations

from datetime import date, timedelta

from dl_control.main import (
    _director_system_prompt,
    _family_skill_queries,
    _family_system_prompt,
    _match_skill_rows,
    _prompt_answer_rules,
    _req_lang,
    _schedule_window,
    _skill_queries,
)

# ---- fakes ----


class _Req:
    def __init__(self, cookies):
        self.cookies = cookies


class _Sess:
    """最小会话对象：prompt 组装只读 name/role/dept/building/floor/residents"""

    def __init__(self, name="王建国", role="director", dept=None, building=None,
                 floor=None, residents=None):
        self.name = name
        self.role = role
        self.dept = dept
        self.building = building
        self.floor = floor
        self.residents = residents


# ---- _req_lang ----


def test_req_lang_cookie_normalized_default_zh():
    """lang cookie 归一：en/zh 直通，缺失/脏值回落 zh（非 i18n.DEFAULT_LANG=en）"""
    assert _req_lang(_Req({"lang": "en"})) == "en"
    assert _req_lang(_Req({"lang": "zh"})) == "zh"
    assert _req_lang(_Req({})) == "zh"
    assert _req_lang(_Req({"lang": "fr"})) == "zh"
    assert _req_lang(_Req({"lang": ""})) == "zh"


# ---- 意图匹配：英文问句 vs 中文等价问句 ----


def _skills(message: str, family: bool = False) -> list[str]:
    table = _family_skill_queries() if family else _skill_queries()
    return [s for s, _q in _match_skill_rows(message, table)]


# (英文问句, 中文等价问句, 期望意图行) —— 演示铁律：问句必须命中意图行才有数据注入
_EN_INTENT_CASES = [
    ("How many residents need full care today?", "院里有多少老人", ["resident-query"]),
    ("What is on the menu for dinner?", "晚饭吃什么", ["meal-query"]),
    ("Any falls reported this week?", "最近有老人摔倒吗", ["alert-query"]),
    ("Show me the inventory of diapers", "尿不湿库存还有多少", ["logistics-inventory"]),
    ("Who is on duty in building 3 tomorrow?", "3号楼明天谁当班", ["nursing-schedule"]),
    ("What is the work order completion rate?", "工单完成率怎么样", ["nursing-work-order"]),
    ("Which residents are due for re-assessment?", "谁该复评", ["assessment-query"]),
    ("Anyone with unpaid fees this month?", "有欠费的老人吗", ["finance-query"]),
    ("Any complaints from families this week?", "有人投诉吗", ["complaint-query"]),
    ("How many beds are vacant right now?", "还有空床吗", ["beds-occupancy"]),
    ("How many nurses and caregivers do we have?", "院里有多少护工", ["staff-query"]),
    ("What activities are planned for today?", "今天有什么活动", ["activity-query"]),
]

_EN_FAMILY_CASES = [
    ("How is my father doing?", "我家老人最近怎么样", ["family-overview"]),
    ("Can I see this month's bill and fees?", "本月账单和费用", ["family-billing"]),
    ("What did he eat for lunch today?", "今天午饭吃什么", ["family-meals"]),
    ("How is his blood pressure lately?", "他血压怎么样", ["family-care"]),
]


def test_intent_en_matches_chinese_equivalents():
    """英文问句逐个命中正确意图行，且命中行数与中文等价问句一致"""
    for en_msg, zh_msg, expected in _EN_INTENT_CASES:
        en_hits = _skills(en_msg)
        zh_hits = _skills(zh_msg)
        assert en_hits == expected, (en_msg, en_hits)
        assert en_hits == zh_hits, (en_msg, zh_msg, en_hits, zh_hits)


def test_intent_en_family_table():
    """家属表四行（billing/meals/care/overview）英文问句同款命中"""
    for en_msg, zh_msg, expected in _EN_FAMILY_CASES:
        en_hits = _skills(en_msg, family=True)
        zh_hits = _skills(zh_msg, family=True)
        assert en_hits == expected, (en_msg, en_hits)
        assert en_hits == zh_hits, (en_msg, zh_msg, en_hits, zh_hits)


def test_intent_en_no_english_keyword_gaps():
    """覆盖钉：员工表每个意图、家属表每一行都至少有一个英文关键词
    （漏一行 = 该意图的英文问句全走兜底 = LLM 编数）"""
    by_skill: dict[str, set] = {}
    for keywords, skill_name, _q in _skill_queries():
        by_skill.setdefault(skill_name, set()).update(keywords)
    for skill_name, kws in by_skill.items():
        assert any(kw.isascii() and kw.islower() for kw in kws), skill_name
    for keywords, skill_name, _q in _family_skill_queries():
        assert any(kw.isascii() and kw.islower() for kw in keywords), skill_name


def test_intent_no_regression_chinese():
    """中文回归钉：原有关键词问句命中不变（抽 5+ 个原口径）"""
    assert _skills("3号楼当班人员") == ["nursing-schedule"]
    assert _skills("工单完成率怎么样") == ["nursing-work-order"]
    assert _skills("尿不湿多少钱") == ["logistics-inventory"]
    assert _skills("食堂有投诉吗") == ["complaint-query"]
    assert _skills("吴桂英欠费三个月了吗") == ["finance-query"]
    assert _skills("本日菜单和活动") == ["meal-query", "activity-query"]  # 组合白名单
    assert _skills("院内通知") == []


# ---- system prompt 语言分支 ----


def test_director_prompt_en_contains_org_intro_and_language():
    sess = _Sess(name="王建国", role="director", dept="院办", building=None, floor=None)
    en = _director_system_prompt(sess, "2026-09-15 Tuesday", None, "hello", "en")
    assert "Respond in English" in en
    assert "Hangzhou Social Welfare Center" in en
    assert "451 Hemu Road" in en
    # 数据分支：中文专名说明 + 回答语言指令
    en_data = _director_system_prompt(
        sess, "2026-09-15 Tuesday", [{"name": "张国栋"}], "who?", "en")
    assert "Respond in English" in en_data
    assert "Chinese proper nouns" in en_data
    assert "张国栋" in en_data


def test_director_prompt_zh_unchanged():
    sess = _Sess(name="王建国", role="director")
    zh = _director_system_prompt(sess, "2026年09月15日 Tuesday", None, "你好")
    assert "用中文" in zh
    assert "杭州市社会福利中心" in zh
    zh_data = _director_system_prompt(sess, "2026年09月15日 Tuesday", [{"a": 1}], "你好")
    assert "用中文直接回答" in zh_data


def test_family_prompt_lang_branches():
    sess = _Sess(name="王丽华", residents="[]")
    en = _family_system_prompt(sess, "2026-09-15 Tuesday", None, "how is father", "en")
    assert "Respond in English" in en
    assert "family-services assistant" in en
    en_data = _family_system_prompt(
        sess, "2026-09-15 Tuesday", [{"name": "张国栋"}], "how is father", "en")
    assert "Respond in English" in en_data
    assert "Chinese proper nouns" in en_data
    zh = _family_system_prompt(sess, "2026年09月15日", None, "近况")
    assert "用中文" in zh  # 缺省 lang=zh，既有调用不传 lang 行为不变


def test_prompt_answer_rules_lang():
    assert "简洁表格" in _prompt_answer_rules()
    assert "简洁表格" in _prompt_answer_rules("zh")
    assert "concise table" in _prompt_answer_rules("en")


# ---- _schedule_window 英文时间词 ----


def test_schedule_window_en():
    today = date.today()
    assert _schedule_window("Who is on duty tomorrow?") == [
        (today + timedelta(days=1)).isoformat()
    ]
    # "day after tomorrow" 含子串 "tomorrow"，必须先整词判定
    assert _schedule_window("What about the day after tomorrow?") == [
        (today + timedelta(days=2)).isoformat()
    ]
    assert _schedule_window("Show the schedule for next 3 days") == [
        (today + timedelta(days=i)).isoformat() for i in range(3)
    ]
    week = _schedule_window("Who is on duty this week?")
    monday = today - timedelta(days=today.weekday())
    assert week is not None and week[0] == monday.isoformat()
    trailing = _schedule_window("Any schedule changes in the last 3 days?")
    assert trailing is not None and trailing[-1] == today.isoformat()
    assert _schedule_window("Show recent schedules") is not None
    # 无时间词 → None（只查当天）
    assert _schedule_window("Who is on duty today?") is None


def test_schedule_window_zh_regression():
    """中文分支行为不变（含「明天后天」双日与「上周」刻意不展开）"""
    today = date.today()
    assert _schedule_window("明天后天呢") == [
        (today + timedelta(days=1)).isoformat(),
        (today + timedelta(days=2)).isoformat(),
    ]
    assert _schedule_window("明天谁当班") == [(today + timedelta(days=1)).isoformat()]
    assert _schedule_window("后天的排班") == [(today + timedelta(days=2)).isoformat()]
    assert _schedule_window("今天谁当班") is None
    assert _schedule_window("上周排班") is None
