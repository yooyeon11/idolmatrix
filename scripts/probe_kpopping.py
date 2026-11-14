"""抓 kpopping 团体页 HTML，列出所有图片 URL 及出现位置（调试用）。"""
import re
import sys

import requests

PROXY = "http://127.0.0.1:7897"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

url = sys.argv[1] if len(sys.argv) > 1 else "https://kpopping.com/profiles/group/RESCENE"
s = requests.Session()
s.proxies = {"http": PROXY, "https": PROXY}
r = s.get(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}, timeout=60)
print("status", r.status_code, "len", len(r.text))
open("_kpopping_page.html", "w", encoding="utf-8").write(r.text)

imgs = re.findall(r'https?://[^"\'\\\s]+?\.(?:png|jpg|jpeg|webp)[^"\'\\\s]*', r.text)
seen = []
for u in imgs:
    if u not in seen:
        seen.append(u)
for u in seen:
    print(u)
print("total", len(seen))
