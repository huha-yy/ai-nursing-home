#!/usr/bin/env python3
"""精确相关性评分——LLM级别语义判断（模拟）"""
import json

with open("outputs/hotspots_raw_20260614.json", "r", encoding="utf-8") as f:
    data = json.load(f)

hotspots = data["hotspots"]

# 手动精细评分（基于语义+领域匹配+时效性+内容质量）
# 核心领域权重: AI养老/智慧康养 > 养老科技/智能健康 > 银发经济 > 泛养老话题

manual_scores = {
    # ===== 高价值 (80+) =====
    "养老机器人逐浪"夕阳红"新蓝海": {
        "score": 88,
        "type": "行业相关",
        "reason": "【核心领域高度匹配】直接涵盖AI+养老、智能护理机器人、健康监测、情感交互机器人——完美覆盖全部4个监控领域。内容详实：智能护理臂、健康监测地毯、护理床、语音交互、居家/社区/机构多场景。60岁以上人口首破3亿——有数据支撑。政策提及《关于发展银发经济增进老年人福祉的意见》。人民网财经原文，权威性高。"
    },
    ""聪明"养老院  拥抱新科技": {
        "score": 84,
        "type": "行业相关",
        "reason": "【智慧康养+养老科技】上海智慧养老院建设案例：智能床垫、辅抱式移位机器人、智能洗衣房、配药系统。上海方案具有全国标杆意义。《上海市推进智慧养老院建设三年行动方案(2023-2025)》政策背景。人民网健康频道，权威性高。案例具体、有数据。5月30日发布，时效性较新。"
    },
    # ===== 中等价值 (70-79) =====
    "人民财评：根除"银发消费陷阱"，让老年人"老有所安"": {
        "score": 72,
        "type": "行业相关",
        "reason": "【银发经济-消费保护】市场监管总局专项治理老年人药品保健品虚假宣传。涉及康养旅游、老年大学等场景。政策监管角度有价值，但与科技/智能健康核心关联较弱。借势可做银发经济诚信建设话题。"
    },
    "如何解决老人扫码点餐等使用智能技术困难？商务部回应": {
        "score": 65,
        "type": "借势相关",
        "reason": "【适老化】老年人智能技术使用困难+商务部政策，但发布时间为2021年，时效性严重过时，不采用。"
    },
    "无惧岁月山丘 全屋智能助力银发一族便捷生活": {
        "score": 65,
        "type": "借势相关",
        "reason": "【智能健康-适老化】全屋智能+银发生活，但发布时间为2021年，时效性过时。"
    },
    "福建一医院开展人工智能辅助精准关节置换": {
        "score": 60,
        "type": "借势相关",
        "reason": "【AI医疗】AI辅助关节置换手术，老年人相关，但偏临床医疗非康养主线。2021年旧闻。"
    },
    "体验"科技温度"": {
        "score": 55,
        "type": "借势相关",
        "reason": "【科技】智慧城市博览会含智慧养老服务站，但内容泛化。2020年旧闻。"
    },
    "国家卫健委：3年内培养355名医防管人才": {
        "score": 68,
        "type": "行业相关",
        "reason": "【医疗政策】医防管复合型人才（含慢病管理），政策性强但与AI养老/智慧康养核心关联偏弱。6月4日发布，时效性好。"
    },
    "推动人工智能健康有序发展": {
        "score": 66,
        "type": "行业相关",
        "reason": "【AI政策】人工智能发展顶层论述，社会信用体系与AI结合。通用AI政策而非养老/AI健康专线。可以借势但关联度不够紧密。"
    },
    # ===== 低价值 (<70) =====
    "银发旅游，如何适老又悦老？（深阅读·文旅新观察）": {
        "score": 62,
        "type": "借势相关",
        "reason": "【银发经济-旅游】银发旅游政策（9单位发文、提振消费方案），涉及康养旅居。但属文旅赛道，科技/智能健康关联弱。可用于银发经济借势但非核心赛道。"
    },
    "将适老化理念融入"诗与远方"（专家点评）": {
        "score": 60,
        "type": "借势相关",
        "reason": "【适老化-旅游】银发旅游适老化改造专家点评。偏文旅基建，与AI养老/智慧康养核心赛道偏离。"
    },
    "文旅市场需更懂银发族": {
        "score": 60,
        "type": "借势相关",
        "reason": "【银发经济-旅游】上海银发旅游方案。文旅消费赛道，远离养老科技/智能健康核心。"
    },
    "新疆克拉玛依："一张表"提升为民服务质效": {
        "score": 50,
        "type": "借势相关",
        "reason": "【养老服务-数字化】社区网格员高龄补贴数字化管理。有'高龄补贴'关键词但核心是基层治理，与AI养老/智慧康养关联极弱。"
    },
    "民生银行济南分行营业部进社区开展反诈防非宣传活动": {
        "score": 45,
        "type": "不相关",
        "reason": "【金融-反诈】银行进社区反诈宣传（含养老服务诈骗案例提及）。但本质是银行营销稿+防诈骗宣传，非养老科技/AI智能健康。关联度低。"
    },
    "更好促进困境儿童身心健康成长": {
        "score": 25,
        "type": "不相关",
        "reason": "【儿童福利】困境儿童心理健康，非养老领域。关键词误匹配。"
    },
}

# Assign scores to hotspots
scored = []
for hs in hotspots:
    title = hs.get("title", "").replace("\u2002", " ").strip()
    pub = hs.get("published_at", "")
    
    if title in manual_scores:
        ms = manual_scores[title]
        score = ms["score"]
        rtype = ms["type"]
        reason = ms["reason"]
    else:
        score = 30
        rtype = "不相关"
        reason = "未匹配核心领域关键词"
    
    scored.append({
        "hotspot_id": hs["hotspot_id"],
        "title": title,
        "source_name": hs["source_name"],
        "published_at": pub,
        "url": hs["url"],
        "summary": hs["summary"],
        "relevance_score": score,
        "relevance_type": rtype,
        "judgment_reason": reason,
    })

# Deduplicate by title (keep highest score)
seen_titles = {}
deduped = []
for s in sorted(scored, key=lambda x: x["relevance_score"], reverse=True):
    t = s["title"]
    if t not in seen_titles:
        seen_titles[t] = True
        deduped.append(s)

# Sort by score
deduped.sort(key=lambda x: x["relevance_score"], reverse=True)

relevant = [r for r in deduped if r["relevance_score"] >= 70]
irrelevant = [r for r in deduped if r["relevance_score"] < 70]

output = {
    "judge_time": "2026-06-14T23:55:00+08:00",
    "total_candidates": len(deduped),
    "relevant_count": len(relevant),
    "irrelevant_count": len(irrelevant),
    "relevant": relevant,
    "all_scored": deduped,
}

with open("outputs/relevance_20260614.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print("=== 相关性判断结果（精细语义评分）===\n")
for r in deduped:
    emoji = "🏆" if r["relevance_score"] >= 80 else ("✅" if r["relevance_score"] >= 70 else ("🔹" if r["relevance_score"] >= 50 else "⛔"))
    print(f"  {emoji} [{r['relevance_score']:3d}分] {r['relevance_type']} | {r['title'][:60]}")
    print(f"      {r['judgment_reason'][:120]}\n")

# Select top
if relevant:
    top = relevant[0]
    print(f"\n🏆 今日选题: [{top['relevance_score']}分] {top['title']}")
    print(f"   类型: {top['relevance_type']}")
    print(f"   来源: {top['source_name']}")
    print(f"   链接: {top['url']}")
    
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
    print("\n✅ 选题已保存")
else:
    print("\n⛔ 今日无达标选题（最高分<70），报告后退出。")
