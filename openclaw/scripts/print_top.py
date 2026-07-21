import json

with open("outputs/relevance_20260617.json", "r", encoding="utf-8") as f:
    data = json.load(f)
rankings = data["rankings"]
for i, hs in enumerate(rankings[:15]):
    title = hs["title"].replace("\u2002", " ").replace("\u2003", " ")
    kws = hs.get("score_details", {}).get("keyword_value", [])[:4]
    print(f"{i + 1}. [{hs['relevance_score']}分/{hs['relevance_type']}] {title[:80]}")
    print(
        f"   URL year: {hs['url'].split('/')[-3] if '/' in hs['url'] else '?'}  KW: {', '.join(kws)}"
    )
