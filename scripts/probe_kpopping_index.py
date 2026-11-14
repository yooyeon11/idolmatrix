"""探测 kpopping 的组合名录页 / 搜索接口，用于建立 name -> slug 索引。"""
import re
import sys

import requests

sys.stdout.reconfigure(encoding="utf-8")
P = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"}

for url in (
    "https://kpopping.com/groups/girl-groups",
    "https://kpopping.com/groups/boy-groups",
):
    r = requests.get(url, proxies=P, headers=UA, timeout=60)
    print(url, r.status_code, len(r.text))
    slugs = re.findall(r'/profiles/group/([A-Za-z0-9%._\-\'’]+)', r.text)
    uniq = list(dict.fromkeys(slugs))
    print("  slugs:", len(uniq), uniq[:15])
    fn = "_kpopping_" + url.rsplit("/", 1)[-1] + ".html"
    open(fn, "w", encoding="utf-8").write(r.text)

for url in (
    "https://kpopping.com/api/search?q=RESCENE",
    "https://kpopping.com/search?q=RESCENE",
):
    try:
        r = requests.get(url, proxies=P, headers=UA, timeout=45)
        print(url, r.status_code, len(r.text), r.headers.get("content-type"))
        print("  ", r.text[:200].replace("\n", " "))
    except Exception as e:
        print(url, "ERR", type(e).__name__, str(e)[:100])
