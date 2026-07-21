#!/usr/bin/env python3
"""
相关性判断：对候选热点打分排序。评分维度：
- 领域精准度（40分）：是否精确命中AI+养老/智慧康养/养老科技/银发经济/智能健康
- 时效性（20分）：是否近期热点
- 内容丰富度（20分）：摘要是否有足够信息量
- 品牌可连接性（20分）：是否可自然连接到戴恩产品
"""

import json
import sys
from datetime import datetime, timezone

# 高价值关键词——精准匹配加分
HIGH_VALUE_KW = {
    "智慧养老": 8,
    "AI养老": 8,
    "养老科技": 8,
    "智能护理": 8,
    "护理机器人": 8,
    "银发经济": 7,
    "智能硬件": 7,
    "康复辅具": 7,
    "长护险": 7,
    "长期护理保险": 7,
    "居家养老": 7,
    "适老化": 6,
    "康养": 6,
    "健康监测": 6,
    "医养结合": 6,
    "养老产业": 6,
    "助浴": 7,
    "服务消费机器人": 7,
    "失能": 7,
    "褥疮": 6,
    "老龄化": 4,
    "养老": 3,
    "机器人": 4,
    "智能": 3,
    "AI": 5,
}

# 降分关键词（太泛、政策类负面/无关）
LOW_VALUE_KW = {
    "骗老": -5,
    "坑老": -5,
    "虚假宣传": -3,
    "诈骗": -5,
}


def compute_score(hs):
    title = hs.get("title", "")
    summary = hs.get("summary", "")
    matched = hs.get("matched_kw", [])
    url = hs.get("url", "")

    score = 0

    # 1. 领域精准度 (40分)
    domain_score = 0
    for kw in matched:
        if kw in HIGH_VALUE_KW:
            domain_score += HIGH_VALUE_KW[kw]
        elif kw in LOW_VALUE_KW:
            domain_score += LOW_VALUE_KW[kw]
    domain_score = min(40, max(0, domain_score))
    score += domain_score

    # 2. 时效性 (20分) — 检查URL中是否有年份
    import re

    year_match = re.search(r"/(\d{4})/", url)
    if year_match:
        year = int(year_match.group(1))
        if year >= 2025:
            score += 20
        elif year >= 2023:
            score += 12
        elif year >= 2021:
            score += 6
        else:
            score += 2
    else:
        score += 15  # no year info, assume recent

    # 3. 内容丰富度 (20分)
    if len(summary) > 200:
        score += 15
    elif len(summary) > 100:
        score += 10
    elif len(summary) > 50:
        score += 5

    if len(title) > 10 and len(title) < 60:
        score += 5
    else:
        score += 2

    # 4. 品牌可连接性 (20分)
    brand_conn_kw = [
        "智能护理",
        "护理机器人",
        "助浴",
        "康复辅具",
        "失能",
        "居家养老",
        "长护险",
        "褥疮",
        "卧床",
        "智能硬件",
        "适老化",
        "健康监测",
    ]
    brand_score = 0
    for kw in matched:
        if kw in brand_conn_kw:
            brand_score += 4
    brand_score = min(20, brand_score)
    score += brand_score

    return score, {
        "domain_precision": domain_score,
        "timeliness": min(20, score - domain_score - brand_score)
        if domain_score + brand_score < score
        else 15,
        "content_richness": min(
            20, 15 if len(summary) > 200 else (10 if len(summary) > 100 else 5)
        ),
        "brand_connectivity": brand_score,
        "keyword_value": [k for k in matched if k in HIGH_VALUE_KW],
    }


def main():
    input_path = sys.argv[1] if len(sys.argv) > 1 else "outputs/hotspots_filtered_20260617.json"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "outputs/relevance_20260617.json"

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    hotspots = data.get("hotspots", [])

    scored = []
    for hs in hotspots:
        score, details = compute_score(hs)
        hs["relevance_score"] = score
        hs["score_details"] = details
        scored.append(hs)

    # Sort
    scored.sort(key=lambda x: x["relevance_score"], reverse=True)

    # Determine relevance type
    for hs in scored:
        s = hs["relevance_score"]
        kw = hs.get("matched_kw", [])
        if any(k in kw for k in ["戴恩", "DANEENON", "智能便携洗浴", "DEN FlexBath", "护理机器人"]):
            hs["relevance_type"] = "直接相关"
        elif any(
            k in kw
            for k in [
                "养老机器人",
                "智慧养老",
                "智能护理",
                "养老科技",
                "AI养老",
                "银发经济",
                "长护险",
                "居家养老",
                "适老化",
            ]
        ):
            hs["relevance_type"] = "行业相关"
        elif s >= 60:
            hs["relevance_type"] = "行业相关"
        elif s >= 40:
            hs["relevance_type"] = "借势相关"
        else:
            hs["relevance_type"] = "不相关"

    # Filter valid (>40)
    valid = [h for h in scored if h["relevance_score"] >= 40]

    output = {
        "judge_time": datetime.now(timezone.utc).isoformat(),
        "total_evaluated": len(scored),
        "valid_count": len(valid),
        "rankings": valid[:30],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"  Scored {len(scored)} hotspots, {len(valid)} passing (>=40)")
    print(f"\n  TOP 10:")
    for i, hs in enumerate(valid[:10]):
        title = hs.get("title", "")[:70]
        kwv = ", ".join(hs.get("score_details", {}).get("keyword_value", [])[:4])
        print(f"  {i + 1}. [{hs['relevance_score']}分/{hs['relevance_type']}] {title}")
        print(f"     KW: {kwv}")

    if valid:
        print(f"\n  选中: {valid[0]['title'][:80]} ({valid[0]['relevance_score']}分)")


if __name__ == "__main__":
    main()
