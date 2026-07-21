"""Task 5: health-signal detection tests.

Verifies the pure detection function — resident name + health keyword matching —
without requiring a database or running server.

Run with:

    uv run pytest tests/test_health_signal.py -v
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Detection function
# ---------------------------------------------------------------------------


def test_detect_resident_and_keyword():
    """Message with resident name + health keyword triggers an alert.

    "感冒了吃什么药好得快" contains both "感冒" (感冒 category) and "吃什么药"
    (用药 category), so the detection returns the cross-product: 1 resident
    x 2 categories = 2 results.
    """
    from dl_control.middleware.health_signal import detect_health_signals

    residents = [{"id": "R001", "name": "张建国"}]
    results = detect_health_signals("张建国咨询：感冒了吃什么药好得快", residents)

    assert len(results) == 2
    categories = {r["category"] for r in results}
    assert "感冒" in categories
    assert "用药" in categories
    for r in results:
        assert r["resident_name"] == "张建国"
        assert r["resident_id"] == "R001"


def test_no_detection_for_normal_message():
    """Message with no health keywords produces no alerts."""
    from dl_control.middleware.health_signal import detect_health_signals

    residents = [{"id": "R001", "name": "张建国"}]
    results = detect_health_signals("今天天气真好", residents)
    assert len(results) == 0


def test_keyword_without_resident():
    """Health keyword present but no resident name mentioned -> no alert."""
    from dl_control.middleware.health_signal import detect_health_signals

    residents = [{"id": "R001", "name": "张建国"}]
    results = detect_health_signals("感冒了吃什么药", residents)
    assert len(results) == 0


def test_resident_without_keyword():
    """Resident name present but no health keyword -> no alert."""
    from dl_control.middleware.health_signal import detect_health_signals

    residents = [{"id": "R002", "name": "李秀兰"}]
    results = detect_health_signals("李秀兰今天参加了太极拳晨练", residents)
    assert len(results) == 0


def test_multiple_residents_and_categories():
    """Multiple residents and multiple keyword categories produce cross-product."""
    from dl_control.middleware.health_signal import detect_health_signals

    residents = [
        {"id": "R001", "name": "张国栋"},
        {"id": "R004", "name": "赵玉芬"},
    ]
    msg = "张国栋说他头晕，赵玉芬的褥疮需要换药"
    results = detect_health_signals(msg, residents)

    # 张国栋 -> 跌倒(头晕), 赵玉芬 -> 皮肤(褥疮)
    assert len(results) >= 2
    resident_names = {r["resident_name"] for r in results}
    categories = {r["category"] for r in results}
    assert "张国栋" in resident_names
    assert "赵玉芬" in resident_names
    assert "跌倒" in categories
    assert "皮肤" in categories


def test_empty_message():
    """Empty message should return no results."""
    from dl_control.middleware.health_signal import detect_health_signals

    residents = [{"id": "R001", "name": "张建国"}]
    assert detect_health_signals("", residents) == []
    assert detect_health_signals("", []) == []


def test_psychological_keyword():
    """Psychological keywords (心理 category) are detected."""
    from dl_control.middleware.health_signal import detect_health_signals

    residents = [{"id": "R029", "name": "曹美凤"}]
    results = detect_health_signals("曹美凤最近很抑郁，需要关注", residents)
    assert len(results) == 1
    assert results[0]["category"] == "心理"


def test_fall_keyword():
    """Fall keywords (跌倒 category) are detected."""
    from dl_control.middleware.health_signal import detect_health_signals

    residents = [{"id": "R012", "name": "马德才"}]
    results = detect_health_signals("马德才昨天夜里摔倒了", residents)
    assert len(results) == 1
    assert results[0]["category"] == "跌倒"


# ---------------------------------------------------------------------------
# Keyword taxonomy
# ---------------------------------------------------------------------------


def test_all_keywords_have_categories():
    """Every keyword maps to exactly one category."""
    from dl_control.middleware.health_signal import HEALTH_KEYWORDS, _KEYWORD_TO_CATEGORY

    for category, keywords in HEALTH_KEYWORDS.items():
        for kw in keywords:
            assert kw in _KEYWORD_TO_CATEGORY
            assert _KEYWORD_TO_CATEGORY[kw] == category
