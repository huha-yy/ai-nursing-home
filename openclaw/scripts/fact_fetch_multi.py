#!/usr/bin/env python3
"""Fetch additional sources for fact cross-verification"""

import urllib.request, ssl, re, json

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE
opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_ctx))
opener.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")]


def fetch_article(url):
    try:
        resp = opener.open(url, timeout=15)
        data = resp.read()
        enc_match = re.search(rb'charset[=]\s*["\']?([^"\'\s;>]+)', data[:2000])
        enc = enc_match.group(1).decode() if enc_match else "utf-8"
        text = data.decode(enc, errors="replace")
        # Extract body
        body_match = re.search(
            r'<div[^>]*class="rm_txt_con[^"]*"[^>]*>(.*?)<div[^>]*class="edit', text, re.DOTALL
        )
        if not body_match:
            body_match = re.search(
                r'<div[^>]*class="text_show[^"]*"[^>]*>(.*?)</div>\s*<div', text, re.DOTALL
            )
        if not body_match:
            body_match = re.search(r"(<p>[\s\S]*?</p>)", text)
        if body_match:
            content = body_match.group(1)
            content = re.sub(r"<[^>]+>", " ", content)
            content = re.sub(r"&nbsp;", " ", content)
            content = re.sub(r"&lt;", "<", content)
            content = re.sub(r"&gt;", ">", content)
            content = re.sub(r"&amp;", "&", content)
            content = re.sub(r"\s+", " ", content).strip()
            return content[:3000]
        return f"BODY_NOT_FOUND"
    except Exception as e:
        return f"FETCH_ERROR: {e}"


# Source 2: 智慧养老院 article (人民网-健康)
url2 = "http://health.people.com.cn/n1/2025/0530/c14739-40490939.html"
content2 = fetch_article(url2)
print("=== SOURCE 2: 人民网-健康 (智慧养老院) ===")
print(content2)
print()

# Source 3: AI健康服务 article
url3 = "http://health.people.com.cn/n1/2025/0603/c14739-40493648.html"
content3 = fetch_article(url3)
print("=== SOURCE 3: 人民网-健康 (AI健康服务) ===")
print(content3)
