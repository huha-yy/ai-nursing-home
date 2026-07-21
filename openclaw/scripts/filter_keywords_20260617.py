#!/usr/bin/env python3
"""
从去重后的热点列表中筛选与监控领域相关的热点。
监控领域：AI+养老、智慧康养、银发经济、养老科技、智能健康
"""

import json
import sys
import re

# 行业关键词（从company_keywords.yaml提取）
industry_kw = [
    # 人群与场景
    "失能",
    "半失能",
    "失能老人",
    "失智老人",
    "瘫痪",
    "卧床",
    "大小便失禁",
    "术后康复",
    "术后护理",
    "居家养老",
    "社区养老",
    "机构养老",
    "养老院",
    "护理院",
    "适老化",
    "康养",
    "长期护理",
    # 产品与技术
    "护理机器人",
    "智能护理",
    "排泄护理",
    "智能护理设备",
    "助浴",
    "便携洗浴",
    "康复辅具",
    "康复辅助器具",
    "智能硬件",
    "健康监测",
    "远程问诊",
    "辅具",
    # 医疗与政策
    "医疗器械",
    "二类医疗器械",
    "康复医疗",
    "医养结合",
    "长期护理保险",
    "长护险",
    "养老服务",
    "养老政策",
    "残疾人保障",
    "护理补贴",
    "高龄补贴",
    "褥疮",
    "压疮",
    "感染防控",
    # 产业
    "智慧养老",
    "养老产业",
    "银发产业",
    "AI养老",
    "银发经济",
    "服务消费机器人",
    # 扩展领域关键词
    "养老",
    "老龄",
    "老年",
    "退休",
    "老龄化",
    "养老科技",
    "AI",
    "人工智能",
    "机器人",
    "大模型",
    "智能",
    "健康",
    "医疗",
    "护理",
    "康复",
    "医保",
    "社保",
    "民政",
    "护工",
    "照护",
    "人口老龄化",
    "老龄化社会",
    "银发",
]


def match_keywords(text, keywords):
    matched = []
    text_lower = text.lower()
    for kw in keywords:
        if kw.lower() in text_lower:
            matched.append(kw)
    return matched


def main():
    input_path = sys.argv[1] if len(sys.argv) > 1 else "outputs/hotspots_dedup_20260617.json"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "outputs/hotspots_filtered_20260617.json"

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    hotspots = data.get("hotspots", data if isinstance(data, list) else [])

    relevant = []
    for hs in hotspots:
        title = hs.get("title", "")
        summary = hs.get("summary", "")
        text = f"{title} {summary}"

        matched = match_keywords(text, industry_kw)
        if matched:
            hs["matched_kw"] = matched
            hs["match_count"] = len(matched)
            relevant.append(hs)

    # Sort by match count desc
    relevant.sort(key=lambda x: x.get("match_count", 0), reverse=True)

    output = {
        "filter_time": data.get("dedup_time", ""),
        "total_input": len(hotspots),
        "relevant_count": len(relevant),
        "hotspots": relevant,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"  {len(hotspots)} → {len(relevant)} relevant hotspots")
    print(f"  Top 20:")
    for i, hs in enumerate(relevant[:20]):
        print(f"    {i + 1}. [{','.join(hs['matched_kw'][:5])}] {hs['title'][:80]}")
    print(f"  Output: {output_path}")


if __name__ == "__main__":
    main()
