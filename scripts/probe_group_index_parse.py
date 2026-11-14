"""从 kpopping 名录页解析 name -> slug 索引。"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

for fn in ("_kpopping_girl-groups.html", "_kpopping_boy-groups.html"):
    h = open(fn, encoding="utf-8").read()
    # 卡片：<a href="/profiles/group/SLUG"> ... <img alt="NAME" ...>
    pairs = re.findall(r'href="/profiles/group/([^"]+)"[^>]*>(.{0,400}?)</a>', h, re.S)
    print(fn, "links", len(pairs))
    for slug, inner in pairs[:5]:
        alt = re.search(r'alt="([^"]*)"', inner)
        title = re.search(r'<h[23][^>]*>([^<]+)<', inner)
        print("   ", slug, "|", alt.group(1) if alt else None, "|", title.group(1) if title else None)
