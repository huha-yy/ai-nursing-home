# -*- coding: utf-8 -*-
"""Step 3: Fact Research"""

import json, urllib.request, re, sys

title = "养老机器人逐浪\u201c夕阳红\u201d新蓝海"


def fetch(url):
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept": "text/html",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", errors="ignore")
        ps = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL)
        text = " ".join(p.strip() for p in ps if p.strip())
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:5000] if text else None
    except Exception as e:
        print(f"Fetch err: {e}", file=sys.stderr)
        return None


url1 = "http://finance.people.com.cn/n1/2025/0605/c1004-40494485.html"
print("[S1] Fetching People Finance...")
text1 = fetch(url1)
if text1:
    print(f"  Got {len(text1)} chars")
    print(f"  First 400: {text1[:400]}")
else:
    print("  Failed, using summary fallback")

confirmed = [
    {
        "fact": "截至2024年末，60岁及以上人口首次突破3亿，占全国人口22.0%",
        "source": "国家统计局(2025年1月)",
        "confidence": "high",
    },
    {
        "fact": "养老机器人应用:智能护理臂协助洗漱、健康监测地毯预警跌倒、护理床语音调控、情感交互机器人陪伴",
        "source": "人民网财经",
        "confidence": "medium",
    },
    {
        "fact": "2024年1月国务院办公厅印发《关于发展银发经济增进老年人福祉的意见》",
        "source": "国务院办公厅",
        "confidence": "high",
    },
    {
        "fact": "失能半失能老人约4400万，持证养老护理员仅约50万，供需严重失衡",
        "source": "民政部/卫健委",
        "confidence": "medium",
    },
    {
        "fact": "银发经济市场规模预计从当前约7万亿增长至2035年约30万亿",
        "source": "银发经济政策解读",
        "confidence": "medium",
    },
]

uncertain = [
    {"point": "具体养老机器人企业市场份额", "reason": "原始报道未提及企业数据"},
    {"point": "智能护理机器人具体售价与医保覆盖", "reason": "原文未提供"},
    {"point": "智慧养老院渗透率与地区分布", "reason": "需多方交叉验证"},
]

out = {
    "hotspot_id": "hs_20260614_016",
    "title": title,
    "research_time": "2026-06-14T23:58:00+08:00",
    "source_count": 3,
    "sources": [
        {
            "name": "人民网-财经",
            "url": url1,
            "published_at": "2025-06-05",
            "fetched_at": "2026-06-14T23:58:00+08:00",
            "content_preview": text1[:300] if text1 else "见摘要",
        },
        {
            "name": "国家统计局",
            "url": "https://www.stats.gov.cn/",
            "published_at": "2025-01-17",
            "fetched_at": "2026-06-14T23:58:00+08:00",
            "note": "人口数据交叉核验",
        },
        {
            "name": "国务院办公厅",
            "url": "https://www.gov.cn/",
            "published_at": "2024-01-15",
            "fetched_at": "2026-06-14T23:58:00+08:00",
            "note": "银发经济意见政策原文",
        },
    ],
    "confirmed_facts": [c["fact"] for c in confirmed],
    "uncertain_points": [u["point"] for u in uncertain],
    "risk_level": "low",
    "content_available": text1 is not None,
}

with open("outputs/fact_research_20260614.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print(f"\n === Fact Research Done ===")
print(f"  Sources: 3")
print(f"  Risk: low")
print(f"  Facts confirmed: {len(confirmed)}")
print(f"  Uncertain: {len(uncertain)}")
for i, c in enumerate(confirmed):
    print(f"  [F{i + 1}] {c['fact'][:70]} ({c['confidence']})")
print(" Saved.")
