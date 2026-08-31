"""家属会话（role="family"）管线单测（2026-08-24，Q6 家属端）。

覆盖 main.py / auth 模块新增的家属路径：
- _erp_headers：家属会话附 X-Family-Token；员工会话回归钉（不带 token）
- _family_skill_queries：全部走 /api/family/*、无 SQL 行、行序（首匹配 break）
- _family_system_prompt：绑定老人名单、只谈数据、越界礼貌拒绝
- try_family_login：委托 ERP 成功/401/超限短路/ERP 不可达（fake httpx+redis+db）
- 角色门：middleware 员工集不含 family；main.py 对话门/员工门分流钉

路由级（POST /auth/family-login 建会话）走活栈 E2E（test_nursing_auth.py 姿态）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dl_control.auth.service import FamilyLoginResult, LoginError, try_family_login
from dl_control.main import (
    _erp_headers,
    _family_skill_queries,
    _family_system_prompt,
)

# ---- fakes ----


class _Sess:
    """最小会话对象：building / family_token / residents 参与 helper 判定"""

    def __init__(self, building=None, family_token=None, residents=None, name="测试"):
        self.building = building
        self.family_token = family_token
        self.residents = residents
        self.name = name


class _FakeRedis:
    def __init__(self):
        self.data: dict[str, int] = {}

    async def get(self, key):
        return self.data.get(key)

    async def incr(self, key):
        self.data[key] = self.data.get(key, 0) + 1
        return self.data[key]

    async def expire(self, key, ttl):
        return True

    async def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.data:
                del self.data[k]
                n += 1
        return n


class _FakeConnCtx:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


class _FakeDB:
    def conn(self, **_kw):
        return _FakeConnCtx()


class _FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """替身 httpx.AsyncClient：post 返回类属性 resp（测试逐个设定）"""

    resp: _FakeResp = _FakeResp(200)
    calls: list = []
    raise_on_post: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeAsyncClient.calls.append({"url": url, "headers": headers, "json": json})
        if _FakeAsyncClient.raise_on_post is not None:
            raise _FakeAsyncClient.raise_on_post
        return _FakeAsyncClient.resp


@pytest.fixture()
def fake_erp(monkeypatch):
    """接管 service.httpx.AsyncClient + write_event，并清空调用记录"""
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.raise_on_post = None
    monkeypatch.setattr("dl_control.auth.service.httpx.AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr("dl_control.auth.service.write_event",
                        lambda *a, **kw: _async_none())
    return _FakeAsyncClient


async def _async_none():
    return None


# ---- _erp_headers：家属令牌 ----


def test_erp_headers_family_sess_gets_family_token(monkeypatch):
    """家属会话 → X-Family-Token（ASCII hex，无需编码）；无 building 不发楼栋头"""
    monkeypatch.setenv("NURSING_ERP_API_KEY", "sk-test")
    headers = _erp_headers(_Sess(family_token="a" * 32))
    assert headers == {"X-API-Key": "sk-test", "X-Family-Token": "a" * 32}
    assert all(v.isascii() for v in headers.values())


def test_erp_headers_family_regression_staff_sessions(monkeypatch):
    """员工回归钉：楼长/管理层/无会话都不带 X-Family-Token"""
    monkeypatch.setenv("NURSING_ERP_API_KEY", "sk-test")
    assert "X-Family-Token" not in _erp_headers(_Sess(building="3号楼"))
    assert "X-Family-Token" not in _erp_headers(_Sess())
    assert "X-Family-Token" not in _erp_headers(None)


def test_erp_headers_blank_family_token_omitted(monkeypatch):
    """family_token 为空串/None → 不发头（fail-closed：ERP 侧令牌缺失即 401）"""
    monkeypatch.setenv("NURSING_ERP_API_KEY", "sk-test")
    for tok in (None, ""):
        assert "X-Family-Token" not in _erp_headers(_Sess(family_token=tok))


# ---- _family_skill_queries ----


def test_family_queries_all_hit_family_api_prefix():
    """全部行都是 API:/api/family/*——不出现 SQL 行（本侧库无家属可看数据）"""
    for keywords, skill, query in _family_skill_queries():
        assert query.startswith("API:/api/family/"), query
        assert "SELECT" not in query
        assert keywords and skill.startswith("family-")


def test_family_queries_row_order():
    """行序即优先级（首匹配 break）：账单 < 吃饭 < 健康 < 泛近况兜底"""
    skills = [s for _k, s, _q in _family_skill_queries()]
    assert skills == ["family-billing", "family-meals", "family-care", "family-overview"]


def test_family_queries_meal_intent_maps_to_meals():
    row = next(r for r in _family_skill_queries() if "吃饭" in r[0])
    assert row[2] == "API:/api/family/meals/"


def test_family_queries_overview_is_catchall_last():
    """兜底行覆盖泛近况词（老人/爸/妈/怎么样）且排在最后"""
    rows = _family_skill_queries()
    row = rows[-1]
    assert row[1] == "family-overview"
    for kw in ("老人", "爸", "妈", "怎么样"):
        assert kw in row[0]


# ---- _family_system_prompt ----


def test_family_prompt_lists_bound_residents():
    sess = _Sess(
        name="王丽华",
        residents=json.dumps([
            {"id": 1, "name": "张国栋", "building": "1号楼", "room": "101", "relation": "子女"},
            {"id": 2, "name": "李秀兰", "building": "1号楼", "room": "102", "relation": "子女"},
        ], ensure_ascii=False),
    )
    prompt = _family_system_prompt(sess, "2026年08月24日", None, "近况")
    assert "王丽华" in prompt
    assert "张国栋（1号楼101，子女）" in prompt
    assert "李秀兰" in prompt
    assert "超出家属服务范围" in prompt  # 越界礼貌拒绝指令


def test_family_prompt_with_skill_data_only_trusts_data():
    sess = _Sess(name="王丽华", residents="[]")
    data = [{"name": "张国栋", "today_meals": "午餐已点"}]
    prompt = _family_system_prompt(sess, "2026年08月24日", data, "吃饭")
    assert "真实照护数据" in prompt
    assert "午餐已点" in prompt
    assert "不要编造" in prompt
    assert "张国栋（" not in prompt  # 无绑定时名单退场，只依据数据


def test_family_prompt_bad_residents_json_degrades():
    """residents JSON 损坏 → 名单退到（暂无绑定老人），不抛异常"""
    sess = _Sess(name="王丽华", residents="{not json")
    prompt = _family_system_prompt(sess, "2026年08月24日", None, "近况")
    assert "暂无绑定老人" in prompt


# ---- try_family_login ----


async def test_family_login_success(fake_erp, monkeypatch):
    monkeypatch.setenv("NURSING_ERP_URL", "http://erp-test:9081/")
    monkeypatch.setenv("NURSING_ERP_API_KEY", "sk-erp")
    fake_erp.resp = _FakeResp(200, {
        "family_id": 7, "token": "t" * 32, "name": "王丽华",
        "residents": [{"id": 1, "name": "张国栋"}],
    })
    redis = _FakeRedis()

    result = await try_family_login(
        _FakeDB(), redis,
        username="13800000001 ", password="123456", ip="1.2.3.4",
        rate_limit_fails=5, rate_limit_window=900,
    )

    assert isinstance(result, FamilyLoginResult)
    assert result.user_id == "family:7"  # 与员工 u0xx 文本 ID 不撞
    assert result.username == "13800000001"  # strip 过
    assert result.token == "t" * 32
    # ERP 调用形状：尾斜杠归一 + X-API-Key + JSON 载荷
    call = fake_erp.calls[0]
    assert call["url"] == "http://erp-test:9081/api/family/auth/"
    assert call["headers"] == {"X-API-Key": "sk-erp"}
    assert call["json"] == {"phone": "13800000001", "password": "123456"}
    assert "login_fail:family:13800000001" not in redis.data  # 成功清计数


async def test_family_login_401_bumps_counter(fake_erp):
    fake_erp.resp = _FakeResp(401)
    redis = _FakeRedis()

    with pytest.raises(LoginError) as ei:
        await try_family_login(
            _FakeDB(), redis,
            username="13800000001", password="wrong", ip="1.2.3.4",
            rate_limit_fails=5, rate_limit_window=900,
        )
    assert ei.value.reason == "invalid_credentials"
    assert redis.data["login_fail:family:13800000001"] == 1


async def test_family_login_rate_limited_short_circuits(fake_erp):
    """超限 → 不再打 ERP（零外呼），直接拒"""
    redis = _FakeRedis()
    redis.data["login_fail:family:13800000001"] = 6
    fake_erp.resp = _FakeResp(200)  # 就算 ERP 会答应也不该被问到

    with pytest.raises(LoginError) as ei:
        await try_family_login(
            _FakeDB(), redis,
            username="13800000001", password="123456", ip="1.2.3.4",
            rate_limit_fails=5, rate_limit_window=900,
        )
    assert ei.value.reason == "rate_limited"
    assert fake_erp.calls == []


async def test_family_login_erp_unreachable(fake_erp):
    fake_erp.raise_on_post = ConnectionError("boom")
    with pytest.raises(LoginError) as ei:
        await try_family_login(
            _FakeDB(), _FakeRedis(),
            username="13800000001", password="123456", ip="1.2.3.4",
            rate_limit_fails=5, rate_limit_window=900,
        )
    assert ei.value.reason == "erp_unreachable"


def test_family_login_result_fields():
    r = FamilyLoginResult(
        user_id="family:7", username="13800000001", name="王丽华",
        token="t" * 32, residents=[{"id": 1}],
    )
    assert r.user_id.startswith("family:")
    assert r.residents == [{"id": 1}]


# ---- 角色门分流钉 ----


def test_middleware_nursing_roles_exclude_family():
    """middleware 员工集不含 family：家属进 dashboard/alerts 等员工路由 → 空 NursingContext"""
    from dl_control.auth import middleware

    assert "family" not in middleware._NURSING_ROLES


def test_main_role_gate_split():
    """main.py 门分流钉：对话六门用 _CHAT_ALLOWED（家属放行），
    其余员工路由门保持 _NURSING_ROLES（家属拒）。
    新增路由（无论对话门还是员工门）都必须同步 bump 计数——08-25 工单改版
    新增 /api/nursing/work-orders 时漏 bump，就是这个钉子第一次抓到漂移。
    """
    src = (Path(__file__).resolve().parent.parent / "dl_control" / "main.py").read_text(
        encoding="utf-8"
    )
    chat_gates = src.count("not in _CHAT_ALLOWED")
    staff_gates = src.count("not in _NURSING_ROLES")
    assert chat_gates == 6, "对话门（chat 页/会话 CRUD×4/发消息）应为 6 处 _CHAT_ALLOWED"
    assert staff_gates == 9, "员工路由门应为 9 处 _NURSING_ROLES（dashboard×2/alerts×3/work-orders×2/workflow×1/reports×1）"
