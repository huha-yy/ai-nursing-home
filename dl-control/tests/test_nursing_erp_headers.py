"""nursing-erp 调用辅助的单元测试（阶段一第二批，2026-08-21）。

覆盖 main.py 模块级 helper：
- _erp_headers：X-API-Key 恒带；楼长会话附 X-Building，管理层无楼栋不发头
- _skill_queries：meal-query 回归钉——不得再指向不存在的 /api/meal-plans/
- _week_start：任何日期都返回周一
- _load_nursing_sess：cookie 缺失/无效/有效三分支
"""

from datetime import datetime

from dl_control.auth.middleware import COOKIE_NAME
from dl_control.main import _erp_headers, _load_nursing_sess, _skill_queries, _week_start


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
    """楼长会话（building=3号楼）→ 附 X-Building，ERP 侧只见本楼"""
    monkeypatch.setenv("NURSING_ERP_API_KEY", "sk-test")
    headers = _erp_headers(_Sess("3号楼"))
    assert headers == {"X-API-Key": "sk-test", "X-Building": "3号楼"}


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
