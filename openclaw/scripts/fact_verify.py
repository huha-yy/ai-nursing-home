#!/usr/bin/env python3
"""Search for supporting data sources to cross-verify key statistics"""

import urllib.request, ssl, re, json

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE
opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_ctx))
opener.addheaders = [("User-Agent", "Mozilla/5.0")]

# Verify the 人口 3.1亿 统计 新闻 via Baidu search (simple HTTP)
try:
    # Try the scitech article about smart elderly care
    url = "http://scitech.people.com.cn/n1/2025/0515/c1007-40481086.html"
    resp = opener.open(url, timeout=10)
    data = resp.read()
    enc_match = re.search(rb'charset[=]\s*["\']?([^"\'\s;>]+)', data[:2000])
    enc = enc_match.group(1).decode() if enc_match else "utf-8"
    text = data.decode(enc, errors="replace")
    # extract title
    title_match = re.search(r"<title>(.*?)</title>", text)
    if title_match:
        print(f"Title: {title_match.group(1)}")
    # extract some body
    body_match = re.search(
        r'<div[^>]*class="rm_txt_con[^"]*"[^>]*>(.*?)<div[^>]*class="edit', text, re.DOTALL
    )
    if body_match:
        content = re.sub(r"<[^>]+>", " ", body_match.group(1))
        content = re.sub(r"\s+", " ", content).strip()
        print(content[:2000])
except Exception as e:
    print(f"ERR: {e}")
