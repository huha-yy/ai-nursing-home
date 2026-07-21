#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相关性判断 v3 - 精细语义评分"""

import json

with open("outputs/hotspots_raw_20260614.json", "r", encoding="utf-8") as f:
    data = json.load(f)

hotspots = data["hotspots"]

# 手动精细评分 - 基于标题前缀匹配
manual_scores = {}

for hs in hotspots:
    title = hs.get("title", "").replace("\u2002", " ").strip()
    pub = hs.get("published_at", "")

    if "养老机器人" in title and "新蓝海" in title:
        score, rtype = 88, "行业相关"
        reason = "核心领域高度匹配——直接涵盖AI养老/智能护理机器人/健康监测/情感交互机器人，完美覆盖全部4个监控领域。60岁以上人口首破3亿有数据支撑。人民网财经原文，权威性高。"
    elif "聪明" in title and "养老院" in title:
        score, rtype = 84, "行业相关"
        reason = "智慧康养+养老科技——上海智慧养老院标杆案例：智能床垫/辅抱式移位机器人/配药系统。上海方案全国标杆意义。人民网健康频道。"
    elif "银发消费陷阱" in title:
        score, rtype = 72, "行业相关"
        reason = "银发经济-消费保护——市场监管总局专项治理老年人药品保健品虚假宣传。涉及康养旅游/老年大学场景。政策监管角度有价值，但科技关联较弱。"
    elif "国家卫健委" in title and "医防管" in title:
        score, rtype = 68, "行业相关"
        reason = "医疗政策——医防管复合型人才(含慢病管理)，6月4日发布时效性好。但与AI养老/智慧康养核心关联偏弱。"
    elif "人工智能健康有序" in title:
        score, rtype = 66, "行业相关"
        reason = "AI政策——人工智能发展顶层论述。通用AI政策而非养老专线。可借势但关联度不够。"
    elif "银发旅游" in title and "适老" in title:
        score, rtype = 62, "借势相关"
        reason = "银发经济-旅游——银发旅游政策(9单位发文、提振消费方案)，涉及康养旅居。但属文旅赛道，科技/智能健康关联弱。"
    elif "适老化理念" in title:
        score, rtype = 60, "借势相关"
        reason = "适老化-旅游——银发旅游适老化专家点评。偏文旅基建，与AI养老核心赛道偏离。"
    elif "文旅市场" in title:
        score, rtype = 60, "借势相关"
        reason = "银发经济-旅游——上海银发旅游方案。文旅消费赛道，远离养老科技核心。"
    elif "新疆" in title and "一张表" in title:
        score, rtype = 50, "借势相关"
        reason = "养老服务-数字化——社区网格员高龄补贴数字化管理。核心是基层治理，与AI养老关联极弱。"
    elif "民生银行" in title:
        score, rtype = 45, "不相关"
        reason = "金融-反诈——银行进社区反诈宣传，本质银行营销稿，非养老科技。"
    elif "困境儿童" in title:
        score, rtype = 25, "不相关"
        reason = "儿童福利——困境儿童心理健康，非养老领域。关键词误匹配。"
    elif pub.startswith("2021") or pub.startswith("2020"):
        score, rtype = 30, "不相关"
        reason = f"时效性过旧({pub})，不予采用。"
    else:
        score, rtype = 20, "不相关"
        reason = "未匹配核心领域关键词。"

    manual_scores[title] = {"score": score, "type": rtype, "reason": reason}

# Build scored list
scored = []
for hs in hotspots:
    title = hs.get("title", "").replace("\u2002", " ").strip()
    pub = hs.get("published_at", "")
    ms = manual_scores.get(title, {"score": 20, "type": "不相关", "reason": "未匹配"})

    scored.append(
        {
            "hotspot_id": hs["hotspot_id"],
            "title": title,
            "source_name": hs["source_name"],
            "published_at": pub,
            "url": hs["url"],
            "summary": hs["summary"],
            "relevance_score": ms["score"],
            "relevance_type": ms["type"],
            "judgment_reason": ms["reason"],
        }
    )

# Deduplicate by title (keep highest score)
seen = {}
deduped = []
for s in sorted(scored, key=lambda x: x["relevance_score"], reverse=True):
    if s["title"] not in seen:
        seen[s["title"]] = True
        deduped.append(s)

deduped.sort(key=lambda x: x["relevance_score"], reverse=True)

relevant = [r for r in deduped if r["relevance_score"] >= 70]
irrelevant_count = len(deduped) - len(relevant)

output = {
    "judge_time": "2026-06-14T23:55:00+08:00",
    "total_candidates": len(deduped),
    "relevant_count": len(relevant),
    "irrelevant_count": irrelevant_count,
    "relevant": relevant,
    "all_scored": deduped,
}

with open("outputs/relevance_20260614.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

# Print results
print("=== Relevance Judge Results (Precision Semantic Scoring) ===\n")
for r in deduped:
    flag = (
        "**"
        if r["relevance_score"] >= 80
        else (
            "OK" if r["relevance_score"] >= 70 else ("~~" if r["relevance_score"] >= 50 else "--")
        )
    )
    print(f"  {flag} [{r['relevance_score']:3d}] {r['relevance_type']} | {r['title'][:60]}")
    print(f"      {r['judgment_reason'][:150]}\n")

# Select top topic
if relevant:
    top = relevant[0]
    print(f"\nTOPIC SELECTED: [{top['relevance_score']}] {top['title']}")
    print(f"  Type: {top['relevance_type']}")
    print(f"  Source: {top['source_name']}")
    print(f"  URL: {top['url']}")

    selected = {
        "hotspot_id": top["hotspot_id"],
        "title": top["title"],
        "url": top["url"],
        "source_name": top["source_name"],
        "relevance_score": top["relevance_score"],
        "relevance_type": top["relevance_type"],
        "judgment_reason": top["judgment_reason"],
        "summary": top["summary"],
        "selected_at": "2026-06-14T23:55:00+08:00",
    }
    with open("outputs/selected_topic_20260614.json", "w", encoding="utf-8") as f:
        json.dump(selected, f, ensure_ascii=False, indent=2)
    print("\nSELECTED topic saved.")
else:
    print("\nNO QUALIFYING TOPIC today (all < 70). Pipeline will exit after reporting.")
