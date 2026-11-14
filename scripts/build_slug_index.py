"""用名录页 slug 匹配 DB 组合名，验证覆盖率（避免逐个 search 触发 429）。"""
import json
import re
import sys
from pathlib import Path

from im_api import Api
from kpopping import Kpopping, norm

sys.stdout.reconfigure(encoding="utf-8")
kp = Kpopping()

slugs: list[str] = []
for url in ("https://kpopping.com/groups/girl-groups", "https://kpopping.com/groups/boy-groups"):
    html = kp.cached_get(url).decode("utf-8", "replace")
    slugs += re.findall(r'href="/profiles/group/([^"#?]+)', html)
slugs = list(dict.fromkeys(slugs))
print("slugs", len(slugs))

index: dict[str, str] = {}
for s in slugs:
    index.setdefault(norm(s), s)
    index.setdefault(norm(s.replace("-", " ")), s)

api = Api()
groups = api.paged("/api/groups")
hit, miss = [], []
for g in groups:
    key = norm(g["name"])
    s = index.get(key)
    if s:
        hit.append((g["id"], g["name"], s))
    else:
        miss.append((g["id"], g["name"]))
print(f"命中 {len(hit)} / {len(groups)}；未命中 {len(miss)}")
for mid, name in miss:
    print("   miss:", name)
Path("kpopping_slug_index.json").write_text(
    json.dumps({str(i): s for i, _n, s in hit}, ensure_ascii=False), encoding="utf-8"
)
