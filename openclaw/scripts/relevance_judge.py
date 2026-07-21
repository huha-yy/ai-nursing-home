#!/usr/bin/env python3
"""Step 2: 相关性判断 - 对热点进行两级过滤打分"""

import json

# Load hotspots
with open("outputs/hotspots_raw_20260614.json", "r", encoding="utf-8") as f:
    data = json.load(f)

hotspots = data["hotspots"]

# 公司关键词
company_kw = [
    "戴恩",
    "DANEENON",
    "戴恩尼诺",
    "戴恩可护",
    "智能排泄物护理机器人",
    "智能护理机器人",
    "DEN FlexBath 360",
    "智能便携洗浴机",
    "回吸式卧床助浴",
    "便携洗浴机",
    "邵林超",
]

industry_kw = [
    "失能",
    "半失能",
    "失能老人",
    "失智老人",
    "瘫痪",
    "卧床",
    "大小便失禁",
    "术后康复",
    "居家养老",
    "社区养老",
    "机构养老",
    "养老院",
    "护理院",
    "适老化",
    "康养",
    "长期护理",
    "银发经济",
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
    "医疗器械",
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
    "智慧养老",
    "养老产业",
    "银发产业",
    "AI养老",
    "服务消费机器人",
]

# 监控领域 = AI+养老、智慧康养、银发经济、养老科技、智能健康
domain_kw = [
    "AI养老",
    "人工智能养老",
    "智慧康养",
    "智慧养老",
    "智能养老",
    "数字养老",
    "科技养老",
    "银发经济",
    "银发产业",
    "银发市场",
    "老年经济",
    "养老科技",
    "智能护理",
    "护理机器人",
    "康复辅具",
    "适老化改造",
    "健康监测",
    "远程医疗",
    "AI健康",
    "数字健康",
    "智慧医疗",
    "长护险",
    "长期护理保险",
    "养老服务",
    "居家养老",
    "社区养老",
    "机构养老",
    "医养结合",
    "失能",
    "高龄",
    "老龄化",
    "外骨骼",
    "智能轮椅",
    "慢病管理",
]


def match_keywords(text, kw_list):
    matched = []
    for kw in kw_list:
        if kw in text:
            matched.append(kw)
    return matched


# Judge each hotspot
results = []
for hs in hotspots:
    title = hs.get("title", "")
    summary = hs.get("summary", "")
    full_text = f"{title} {summary}"

    direct = match_keywords(full_text, company_kw)
    industry = match_keywords(full_text, industry_kw)
    domain = match_keywords(full_text, domain_kw)

    # Skip old articles (2021 and earlier)
    pub = hs.get("published_at", "")
    if pub.startswith("2021") or pub.startswith("2020"):
        relevance_score = 0
        relevance_type = "不相关"
        reason = "时效性过旧（2021年），不予采用"
    elif direct:
        relevance_score = 90
        relevance_type = "直接相关"
        reason = f"直接涉及公司/品牌/产品: {', '.join(direct)}"
    elif len(domain) >= 3:
        relevance_score = 80
        relevance_type = "行业相关"
        reason = f"强领域匹配: {', '.join(domain[:5])}"
    elif len(domain) >= 1 or len(industry) >= 3:
        relevance_score = 70
        relevance_type = "行业相关"
        reason = f"领域/行业匹配: {', '.join((domain + industry)[:5])}"
    elif len(industry) >= 1:
        relevance_score = 50
        relevance_type = "借势相关"
        reason = f"部分行业关键词: {', '.join(industry[:3])}"
    else:
        relevance_score = 20
        relevance_type = "不相关"
        reason = "关键词匹配不足，与核心领域无关"

    results.append(
        {
            "hotspot_id": hs["hotspot_id"],
            "title": title,
            "source_name": hs["source_name"],
            "published_at": pub,
            "relevance_score": relevance_score,
            "relevance_type": relevance_type,
            "matched_domain": domain[:5],
            "matched_industry": industry[:5],
            "judgment_reason": reason,
        }
    )

# Sort by score desc
results.sort(key=lambda x: x["relevance_score"], reverse=True)

output = {
    "judge_time": "2026-06-14T23:53:00+08:00",
    "relevant": [r for r in results if r["relevance_score"] >= 50],
    "irrelevant_count": sum(1 for r in results if r["relevance_score"] < 50),
}

with open("outputs/relevance_20260614.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print("=== 相关性判断结果 ===")
for r in output["relevant"]:
    flag = "⭐" if r["relevance_score"] >= 80 else ("✓" if r["relevance_score"] >= 70 else "○")
    print(f"  {flag} [{r['relevance_score']}分] {r['relevance_type']} | {r['title'][:60]}")
    print(f"      理由: {r['judgment_reason'][:100]}")
    print()

print(f"不相关: {output['irrelevant_count']}条")
print(f"\n🏆 TOP 1: [{results[0]['relevance_score']}分] {results[0]['title']}")

# Select top topic
top = results[0]
print(f"\n=== 选定选题 ===")
print(f"  标题: {top['title']}")
print(f"  分数: {top['relevance_score']}")
print(f"  类型: {top['relevance_type']}")
print(f"  来源: {top['source_name']}")

with open("outputs/selected_topic_20260614.json", "w", encoding="utf-8") as f:
    json.dump(top, f, ensure_ascii=False, indent=2)
