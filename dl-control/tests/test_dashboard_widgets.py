"""大屏增强五组件的模块级 helper 单测（2026-08-25）。

覆盖 main.py 新增的纯函数（不触网、不触库）：
- _today_cn / _today_menu：week-menu 行按中文星期过滤 + 早/午/晚排序，
  菜名+类别（主食/汤/素菜/荤菜/小菜）透传供前端配色
- _order_stats：按餐次分拉的订单 → 状态聚合（退餐不计 total 但保留计数）+ 特殊餐
- _care_level_pie：care_level 计数、档位定序、空值归"未定"、表外档位追加
- _occupancy_summary：楼栋行求和 + 百分比取整 + 无床位 None
- dashboard API 装配钉：返回 dict 必含五个新键（源码扫描，防漏接线）

ERP payload 形状取自 2026-08-25 生产实测（week-menu 的 day 是中文"周二"；
meal-orders 分页 items；beds/occupancy 的 buildings 行）。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from dl_control import main as m

# ---- _today_cn / _today_menu ----


def _fake_dt_cls(weekday: int):
    """造一个只暴露 now() 的替身 datetime 类（2026-08-24 是周一）"""

    class _FakeDT:
        @staticmethod
        def now():
            return _dt.datetime(2026, 8, 24 + weekday)

    return _FakeDT


def test_today_cn_all_seven_days(monkeypatch):
    """周一…周日七个取值全对（ERP WeekMenu.day 的取值域）"""
    for wd, cn in enumerate(["周一", "周二", "周三", "周四", "周五", "周六", "周日"]):
        monkeypatch.setattr(m, "datetime", _fake_dt_cls(wd))
        assert m._today_cn() == cn


def test_today_menu_filters_and_sorts(monkeypatch):
    """全周行 → 只留今日 + 早/午/晚定序（输入乱序）；菜名+类别透传"""
    monkeypatch.setattr(m, "datetime", _dt.datetime)  # 真实今天
    cn = m._today_cn()
    rows = [
        {"day": cn, "meal_type": "午餐",
         "dishes": [{"id": 1, "name": "香菇炖鸡", "category": "荤菜"}]},
        {"day": cn, "meal_type": "早餐",
         "dishes": [{"id": 2, "name": "八宝粥", "category": "主食"}, {"id": 3, "name": ""}]},
        {"day": "周五", "meal_type": "早餐", "dishes": [{"id": 4, "name": "别的一天"}]},
        {"day": cn, "meal_type": "晚餐", "dishes": [{"id": 5, "name": "蒜蓉菠菜"}]},  # 无类别
    ]
    out = m._today_menu(rows)
    assert [r["meal_type"] for r in out] == ["早餐", "午餐", "晚餐"]
    assert out[0]["dishes"] == [{"name": "八宝粥", "category": "主食"}]  # 空名菜剔除
    assert out[1]["dishes"] == [{"name": "香菇炖鸡", "category": "荤菜"}]
    assert out[2]["dishes"] == [{"name": "蒜蓉菠菜", "category": ""}]  # 缺类别透传空串
    assert all(r["meal_type"] != "别的一天" for r in out)


def test_today_menu_empty_when_week_not_published():
    """整周未排菜单 → 空列表（前端显示"今日菜单未发布"）"""
    assert m._today_menu([]) == []
    assert m._today_menu([{"day": "周五", "meal_type": "早餐", "dishes": []}]) == []


# ---- _order_stats ----


def test_order_stats_aggregates_and_excludes_cancelled_from_total():
    """状态聚合：cancelled 不进 total（退餐不算今日餐单）但计数保留"""
    per_meal = {
        "早餐": [
            {"status": "ordered", "special_requests": ""},
            {"status": "ordered", "special_requests": "低糖"},
            {"status": "cancelled", "special_requests": "软食"},
        ],
        "午餐": [
            {"status": "delivered", "special_requests": None},
            {"status": "modified", "special_requests": "  "},  # 纯空白不算特殊餐
        ],
        "晚餐": [],
    }
    out = m._order_stats(per_meal)
    breakfast = out["meals"][0]
    assert breakfast["total"] == 2  # 3 单 - 1 退
    assert breakfast["special"] == 1  # 只数低糖；退餐的"软食"不再劳烦厨房
    assert out["meals"][1]["by_status"] == {"delivered": 1, "modified": 1}
    assert out["meals"][1]["special"] == 0
    assert out["meals"][2] == {"meal_type": "晚餐", "total": 0, "special": 0, "by_status": {}}
    assert out["total"] == 4  # 2 + 2 + 0
    assert out["special"] == 1
    assert out["by_status"] == {"ordered": 2, "cancelled": 1, "delivered": 1, "modified": 1}


def test_order_stats_fixed_meal_order_ignores_unknown_keys():
    """只认 早/午/晚 三餐；per_meal 里的杂键（如手滑写入"夜宵"）被丢弃"""
    out = m._order_stats({"夜宵": [{"status": "ordered"}], "晚餐": [{"status": "ordered"}]})
    assert [mm["meal_type"] for mm in out["meals"]] == ["早餐", "午餐", "晚餐"]
    assert out["total"] == 1


# ---- _care_level_pie ----


def test_care_level_pie_orders_and_counts():
    """档位定序 自理→半护→全护→失智，空值归"未定"，表外档位尾部追加"""
    residents = [
        {"care_level": "失智"}, {"care_level": "自理"}, {"care_level": "自理"},
        {"care_level": None}, {"care_level": "  "},  # 两例无档位 → 未定×2
        {"care_level": "特护"}, {"care_level": "半护"},
    ]
    out = m._care_level_pie(residents)
    assert out == [
        {"name": "自理", "value": 2},
        {"name": "半护", "value": 1},
        {"name": "失智", "value": 1},
        {"name": "特护", "value": 1},
        {"name": "未定", "value": 2},  # 兜底档永远最后
    ]


def test_care_level_pie_empty():
    assert m._care_level_pie([]) == []


# ---- _today_activities ----


def test_today_activities_sorts_and_shapes():
    """time 文本升序（字典序即时间序）、空 time/空 location 兜底、date 透传"""
    rows = [
        ("2026-08-25", "棋牌友谊赛", "14:00-16:00", "活动中心棋牌室"),
        ("2026-08-25", "晨间操·八段锦", "09:00-09:30", "各楼栋一层大厅"),
        ("2026-08-25", "时间待定的活动", None, None),
    ]
    out = m._today_activities(rows)
    assert out["date"] == "2026-08-25"
    # 空时间垫最后而不是排最前（空串字典序最小，须显式下压）
    assert [i["title"] for i in out["items"]] == ["晨间操·八段锦", "棋牌友谊赛", "时间待定的活动"]
    assert out["items"][2] == {"title": "时间待定的活动", "time": "", "location": ""}


def test_today_activities_empty():
    """无活动（表空/全被日期过滤）→ date None + 空列表（前端显示"今日无活动安排"）"""
    assert m._today_activities([]) == {"date": None, "items": []}


# ---- _occupancy_summary ----


def test_occupancy_summary_sums_and_rounds():
    """楼栋行求和 + 百分比四舍五入（11/12 → 92）"""
    buildings = [
        {"building": "1号楼", "total": 6, "occupied": 6, "free": 0},
        {"building": "2号楼", "total": 6, "occupied": 5, "free": 1},
    ]
    assert m._occupancy_summary(buildings) == {
        "total": 12, "occupied": 11, "free": 1, "rate": 92,
    }


def test_occupancy_summary_no_beds():
    """无床位（ERP 挂了回空 buildings）→ rate None，不除零"""
    assert m._occupancy_summary([])["rate"] is None


# ---- dashboard API 装配钉（防五组件漏接线）----


def test_dashboard_api_returns_new_widget_keys():
    """源码钉：/api/nursing/dashboard 返回 dict 必含五个新键"""
    src = (Path(__file__).resolve().parent.parent / "dl_control" / "main.py").read_text(
        encoding="utf-8"
    )
    for key in ("today_menu", "order_stats", "care_level_distribution",
                "assessment_review", "occupancy", "today_activities"):
        assert f'"{key}": {key}' in src, f"dashboard 返回缺 {key} 接线"
