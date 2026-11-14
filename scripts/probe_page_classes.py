import collections
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
h = open("_kpopping_page.html", encoding="utf-8").read()
cls = re.findall(r'class="([^"]{0,300})"', h)
c: collections.Counter = collections.Counter()
for s in cls:
    for t in s.split():
        if "aspect" in t or "poster" in t.lower() or "portrait" in t.lower():
            c[t] += 1
print(c.most_common(30))
print("poster count:", len(re.findall("poster", h, re.I)))
print("aspect tokens:", sorted(set(re.findall(r"aspect-[A-Za-z0-9\[\]/\.\-]+", h)))[:30])
# 找 member 卡片的 img 附近结构
for m in re.finditer(r'<img[^>]+idols/[^>]+>', h):
    seg = h[max(0, m.start() - 600) : m.start()]
    print("----")
    print(seg[-600:].replace("><", ">\n<"))
