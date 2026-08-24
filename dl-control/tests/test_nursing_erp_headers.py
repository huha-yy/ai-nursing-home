"""nursing-erp 调用辅助的单元测试（阶段一第二批，2026-08-21）。

覆盖 main.py 模块级 helper：
- _erp_headers：X-API-Key 恒带；楼长会话附 X-Building，管理层无楼栋不发头
- _skill_queries：meal-query 回归钉——不得再指向不存在的 /api/meal-plans/；
  财务行接入 /api/billing/（欠费→ arrears 名单、泛财务→ summary、餐费仍走月结）；
  评估行接入 /api/assessments/（盘点类→ review，泛评估→ 列表；行序在
  logistics/resident 之前）
- _erp_items：分页/聚合/纯标量三种 ERP 响应形状的行提取（2026-08-24 模块级化）
- _week_start：任何日期都返回周一
- _load_nursing_sess：cookie 缺失/无效/有效三分支
"""

from datetime import datetime

from dl_control.auth.middleware import COOKIE_NAME
from dl_control.main import (
    _erp_headers,
    _erp_items,
    _load_nursing_sess,
    _skill_queries,
    _week_start,
)


class _Sess:
    """最小会话对象：只有 building 属性参与 _erp_headers"""

    def __init__(self, building):
        self.building = building


class _FakeSessions:
    def __init__(self, sess=None):
        self.sess = sess

    def unsign(self, raw):
        return "sid-1" if raw == "signed-ok" else None

    async def load(self, sid):
        return self.sess if sid == "sid-1" else None


class _Req:
    def __init__(self, cookies):
        self.cookies = cookies


# ---- _erp_headers ----


def test_erp_headers_key_only(monkeypatch):
    monkeypatch.setenv("NURSING_ERP_API_KEY", "sk-test")
    assert _erp_headers() == {"X-API-Key": "sk-test"}


def test_erp_headers_building_sess_gets_header(monkeypatch):
    """楼长会话（building=3号楼）→ 附 X-Building，percent-encode 后纯 ASCII"""
    monkeypatch.setenv("NURSING_ERP_API_KEY", "sk-test")
    headers = _erp_headers(_Sess("3号楼"))
    assert headers == {"X-API-Key": "sk-test", "X-Building": "3%E5%8F%B7%E6%A5%BC"}
    assert all(v.isascii() for v in headers.values())  # httpx 只收 ASCII 头值


def test_erp_headers_management_no_building(monkeypatch):
    """管理层会话（building=None 或 ""）→ 不发 X-Building → ERP 返回全院"""
    monkeypatch.setenv("NURSING_ERP_API_KEY", "sk-test")
    for building in (None, ""):
        assert "X-Building" not in _erp_headers(_Sess(building))
    assert "X-Building" not in _erp_headers(None)  # 无会话同理


# ---- _skill_queries ----


def test_skill_queries_no_meal_plans_regression():
    """回归钉：meal-query 原先指向不存在的 /api/meal-plans/（静默 404）"""
    for _keywords, _skill, query in _skill_queries():
        assert "meal-plans" not in query


def test_skill_queries_meal_query_hits_week_menu():
    entry = next(q for k, s, q in _skill_queries() if s == "meal-query")
    assert entry.startswith("API:/api/week-menu/?week_start=")
    week_start = entry.rsplit("=", 1)[1]
    datetime.strptime(week_start, "%Y-%m-%d")  # 合法日期
    assert week_start == _week_start()


# ---- _skill_queries 财务行（2026-08-24 接入 /api/billing/）----


def test_skill_queries_arrears_row_hits_billing_arrears():
    """欠费类意图 → ERP 欠费名单（跨月累计），不再只看餐费月结"""
    row = next(r for r in _skill_queries() if "欠费" in r[0])
    assert row[1] == "finance-query"
    assert row[2] == "API:/api/billing/arrears/"


def test_skill_queries_generic_finance_row_hits_billing_summary():
    """泛财务词（费用/账单/应收…）→ 当月三额汇总（ERP 侧缺省当月）"""
    row = next(r for r in _skill_queries() if "费用" in r[0])
    assert row[1] == "finance-query"
    assert row[2] == "API:/api/billing/summary/"


def test_skill_queries_arrears_row_precedes_generic_finance():
    """行序即优先级（首匹配 break）：'谁欠费' 必须命中 arrears 而非 summary"""
    rows = _skill_queries()
    i_arrears = next(i for i, r in enumerate(rows) if "欠费" in r[0])
    i_generic = next(i for i, r in enumerate(rows) if "费用" in r[0])
    assert i_arrears < i_generic


def test_skill_queries_meal_finance_row_kept():
    """餐费/月结细分意图仍走 /api/meal-finance/（按人餐费行）"""
    row = next(r for r in _skill_queries() if "餐费" in r[0])
    assert row[1] == "finance-query"
    assert row[2] == "API:/api/meal-finance/"


# ---- _skill_queries 评估行（2026-08-24 接入 /api/assessments/）----


def test_skill_queries_review_row_hits_assessments_review():
    """盘点类意图（待评估/复评/评估盘点）→ 三态盘点（国标 12 个月复评）"""
    row = next(r for r in _skill_queries() if "待评估" in r[0])
    assert row[1] == "assessment-query"
    assert row[2] == "API:/api/assessments/review/"


def test_skill_queries_generic_assessment_row_hits_list():
    """泛评估词（评估/定级/能力等级…）→ 评估单列表"""
    row = next(r for r in _skill_queries() if "定级" in r[0])
    assert row[1] == "assessment-query"
    assert row[2] == "API:/api/assessments/"


def test_skill_queries_assessment_rows_precede_logistics_and_resident():
    """行序即优先级（首匹配 break）三连钉：
    - review 行先于泛评估行（"待评估"含子串"评估"）
    - 评估两行先于 logistics"盘点"行（否则"评估盘点"被吞成库存查询）
    - 评估两行先于 resident"老人"行（"老人的评估"同理）
    """
    rows = _skill_queries()

    def idx(pred):
        return next(i for i, r in enumerate(rows) if pred(r))

    i_review = idx(lambda r: "待评估" in r[0])
    i_generic = idx(lambda r: "定级" in r[0])
    i_logi = idx(lambda r: "盘点" in r[0] and r[1] == "logistics-inventory")
    i_res = idx(lambda r: r[1] == "resident-query")
    assert i_review < i_generic < i_logi < i_res


# ---- _erp_items（2026-08-24 模块级化，支撑 billing 响应形状）----


def test_erp_items_paginated_dict():
    assert _erp_items({"items": [1, 2], "count": 2}) == [1, 2]


def test_erp_items_arrears_dict_prepends_scalars():
    """聚合 dict：汇总标量置顶成一行 + rows，LLM 不丢 total_outstanding"""
    data = {
        "month": "2026-08", "resident_count": 2, "total_outstanding": 46675.0,
        "rows": [{"resident_name": "吴桂英"}, {"resident_name": "王秀兰"}],
    }
    out = _erp_items(data)
    assert out[0] == {"month": "2026-08", "resident_count": 2,
                      "total_outstanding": 46675.0}
    assert out[1:] == data["rows"]


def test_erp_items_summary_dict_wrapped_as_single_row():
    """纯标量 dict（/api/billing/summary/ 三额勾稽）→ 包成单行列表"""
    data = {"month": "2026-08", "count": 36, "receivable": 104500.0,
            "received": 70000.0, "outstanding": 34500.0}
    assert _erp_items(data) == [data]


def test_erp_items_plain_list_and_garbage():
    assert _erp_items([{"a": 1}]) == [{"a": 1}]
    assert _erp_items("junk") == []
    assert _erp_items(None) == []


# ---- _week_start ----


def test_week_start_is_always_monday():
    result = _week_start()
    today = datetime.now().date()
    monday = today - __import__("datetime").timedelta(days=today.weekday())
    assert result == monday.isoformat()
    if today.weekday() == 0:
        assert result == today.isoformat()  # 周一当天：本周一就是今天


# ---- _load_nursing_sess ----


async def test_load_nursing_sess_branches():
    sess = _Sess("1号楼")
    store = _FakeSessions(sess)

    # 无 cookie → None
    assert await _load_nursing_sess(_Req({}), store) is None
    # cookie 无法签名 → None
    assert await _load_nursing_sess(_Req({COOKIE_NAME: "garbage"}), store) is None
    # 有效 cookie → 会话对象
    got = await _load_nursing_sess(_Req({COOKIE_NAME: "signed-ok"}), store)
    assert got is sess
