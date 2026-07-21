#!/usr/bin/env python3
"""批量拉取RSS源并输出标准化热点列表"""

import json
import sys
import re
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

# 北京时间
CST = timezone(timedelta(hours=8))

RSS_SOURCES = [
    # 政策类
    {
        "name": "人民网-时政",
        "url": "http://www.people.com.cn/rss/politics.xml",
        "category": "policy",
    },
    {
        "name": "网易财经-政策信息",
        "url": "http://money.163.com/special/00251L9M/stock_zcxx.xml",
        "category": "policy",
    },
    # 行业聚焦
    {
        "name": "人民网-健康",
        "url": "http://www.people.com.cn/rss/health.xml",
        "category": "industry",
    },
    {
        "name": "人民网-社会",
        "url": "http://www.people.com.cn/rss/society.xml",
        "category": "industry",
    },
    {
        "name": "网易财经-行业透视",
        "url": "http://money.163.com/special/00251L9M/stock_hyts.xml",
        "category": "industry",
    },
    # 科技/商业
    {"name": "36氪", "url": "https://36kr.com/feed", "category": "tech_business"},
    {
        "name": "人民网-科技",
        "url": "http://www.people.com.cn/rss/scitech.xml",
        "category": "tech_business",
    },
    # 财经
    {
        "name": "每日经济新闻",
        "url": "http://money.163.com/special/00251HO9/read_mrjjxw.xml",
        "category": "finance",
    },
    {
        "name": "第一财经日报",
        "url": "http://money.163.com/special/00251HO9/read_dycj.xml",
        "category": "finance",
    },
    {
        "name": "21世纪经济报道",
        "url": "http://money.163.com/special/00251HO9/21sj.xml",
        "category": "finance",
    },
    # 综合
    {
        "name": "人民网-财经",
        "url": "http://www.people.com.cn/rss/finance.xml",
        "category": "general",
    },
    {
        "name": "人民网-观点",
        "url": "http://www.people.com.cn/rss/opinion.xml",
        "category": "general",
    },
]

# 监控领域关键词
DOMAIN_KEYWORDS = [
    # AI + 养老
    "AI养老",
    "人工智能养老",
    "人工智能.*养老",
    "AI.*养老",
    # 智慧康养
    "智慧康养",
    "智慧养老",
    "智能养老",
    "智慧健康养老",
    "数字养老",
    "科技养老",
    "互联网\\+养老",
    # 银发经济
    "银发经济",
    "银发产业",
    "银发市场",
    "老年经济",
    "老龄产业",
    "老年消费",
    # 养老科技
    "养老科技",
    "智能护理",
    "护理机器人",
    "康复辅助",
    "适老化改造",
    "健康监测",
    "远程医疗",
    "可穿戴.*老人",
    "老人.*智能",
    # 智能健康
    "智能健康",
    "健康管理.*智能",
    "AI健康",
    "数字健康",
    "智慧医疗",
    # 养老政策/保险
    "长护险",
    "长期护理保险",
    "养老服务",
    "养老政策",
    "养老保障",
    "养老金",
    "居家养老",
    "社区养老",
    "机构养老",
    "医养结合",
    "失能",
    "高龄",
    "老龄化",
    # 科技/硬件
    "康复辅具",
    "外骨骼",
    "助行器",
    "智能轮椅",
    "健康大数据",
    "慢病管理",
]


def fetch_rss(url, name):
    """拉取RSS源"""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; RSS Reader/1.0)",
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        return raw
    except Exception as e:
        print(f"  ✗ {name}: {e}", file=sys.stderr)
        return None


def parse_rss(raw, source_name, source_category):
    """解析RSS XML为热点条目"""
    entries = []
    try:
        root = ET.fromstring(raw)
    except:
        return entries

    # RSS 2.0
    for item in root.iter("item"):
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        desc = item.findtext("description", "").strip()
        pubdate = item.findtext("pubDate", "")

        # 清理HTML标签
        desc_clean = re.sub(r"<[^>]+>", " ", desc).strip() if desc else ""

        if title:
            entries.append(
                {
                    "title": title,
                    "url": link,
                    "summary": desc_clean[:300],
                    "source_name": source_name,
                    "source_category": source_category,
                    "published_at": pubdate,
                }
            )

    # Atom
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("atom:entry", ns) or root.findall(
        "{http://www.w3.org/2005/Atom}entry"
    ):
        title = entry.findtext("{http://www.w3.org/2005/Atom}title", "").strip()
        link_elem = entry.find("{http://www.w3.org/2005/Atom}link")
        link = link_elem.get("href", "") if link_elem is not None else ""
        summary = entry.findtext("{http://www.w3.org/2005/Atom}summary", "").strip()
        updated = entry.findtext("{http://www.w3.org/2005/Atom}updated", "")

        desc_clean = re.sub(r"<[^>]+>", " ", summary).strip() if summary else ""

        if title:
            entries.append(
                {
                    "title": title,
                    "url": link,
                    "summary": desc_clean[:300],
                    "source_name": source_name,
                    "source_category": source_category,
                    "published_at": updated,
                }
            )

    return entries


def domain_match(title, summary):
    """检查是否匹配监控领域"""
    text = f"{title} {summary}"
    for kw in DOMAIN_KEYWORDS:
        if re.search(kw, text, re.IGNORECASE):
            return True
    return False


def main():
    now_utc = datetime.now(timezone.utc)
    print(f"=== 热点监控开始 ({now_utc.isoformat()}) ===\n")

    all_entries = []
    source_stats = {}

    for src in RSS_SOURCES:
        name, url, cat = src["name"], src["url"], src["category"]
        print(f"  {name} ... ", end="", flush=True)
        raw = fetch_rss(url, name)
        if raw is None:
            source_stats[name] = {"status": "failed", "entries": 0, "matched": 0}
            continue

        try:
            entries = parse_rss(raw, name, cat)
        except Exception as e:
            print(f"parse error: {e}")
            source_stats[name] = {"status": "parse_error", "entries": 0, "matched": 0}
            continue

        matched = [e for e in entries if domain_match(e["title"], e["summary"])]
        source_stats[name] = {"status": "ok", "entries": len(entries), "matched": len(matched)}
        print(f"{len(entries)}条, 领域匹配{len(matched)}条")
        all_entries.extend(matched)

    # 去重：按标题相似度简单去重
    print(f"\n--- 领域匹配总计: {len(all_entries)} 条 ---")

    # 标准化输出
    hotspots = []
    for i, entry in enumerate(all_entries):
        hotspots.append(
            {
                "hotspot_id": f"hs_20260614_{i + 1:03d}",
                "title": entry["title"],
                "url": entry["url"],
                "source_name": entry["source_name"],
                "source_category": entry["source_category"],
                "published_at": entry.get("published_at", ""),
                "summary": entry["summary"],
                "tags": [],
                "duplicate_of": None,
                "raw_sources": 1,
            }
        )

    output = {
        "monitor_time": now_utc.isoformat(),
        "total_hotspots": len(hotspots),
        "source_stats": source_stats,
        "hotspots": hotspots,
    }

    out_path = "outputs/hotspots_raw_20260614.json"
    import os

    os.makedirs("outputs", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n输出: {out_path}")
    print(f"总热点数: {len(hotspots)}")

    # 打印前10条
    for h in hotspots[:20]:
        print(f"  [{h['source_name']}] {h['title'][:80]}")
    if len(hotspots) > 20:
        print(f"  ... 还有 {len(hotspots) - 20} 条")


if __name__ == "__main__":
    main()
