#!/usr/bin/env python3
import urllib.request, ssl, re

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE
opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_ctx))
opener.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")]


def fetch_title_body(url):
    try:
        resp = opener.open(url, timeout=15)
        data = resp.read()
        enc_match = re.search(rb'charset[=]\s*["\']?([^"\'\s;>]+)', data[:2000])
        enc = enc_match.group(1).decode() if enc_match else "utf-8"
        text = data.decode(enc, errors="replace")
        # Extract body - people.com.cn
        body_match = re.search(
            r'<div[^>]*class="rm_txt_con[^"]*"[^>]*>(.*?)<div[^>]*class="edit', text, re.DOTALL
        )
        if not body_match:
            body_match = re.search(
                r'<div[^>]*class="text_show[^"]*"[^>]*>(.*?)</div>\s*<div', text, re.DOTALL
            )
        if not body_match:
            body_match = re.search(r"<p>(.*?)</p>", text)
        if body_match:
            content = body_match.group(1)
            content = re.sub(r"<[^>]+>", " ", content)
            content = re.sub(r"&nbsp;", " ", content)
            content = re.sub(r"\s+", " ", content).strip()
            return content
        return "NO_BODY"
    except Exception as e:
        return f"ERR: {e}"


# Try the 适老化旅游 article (also people.com.cn, might have relevant data)
url = "http://travel.people.com.cn/n1/2025/0526/c41570-40488251.html"
content = fetch_title_body(url)
print("=== 适老化旅游 article ===")
print(content[:2000] if len(content) > 50 else content)
print()

# Try 36kr 具身机器人 article
try:
    url36 = "https://36kr.com/p/3274689123456789"
    resp = opener.open(url36, timeout=10)
    data = resp.read().decode("utf-8", errors="replace")
    # extract from 36kr
    body_match = re.search(
        r'"articleDetail"\s*:\s*\{[^}]*"content"\s*:\s*"(.*?)","', data, re.DOTALL
    )
    if body_match:
        content = body_match.group(1)[:2000]
        print("=== 36kr article ===")
        print(content)
except Exception as e:
    print(f"36kr err: {e}")
