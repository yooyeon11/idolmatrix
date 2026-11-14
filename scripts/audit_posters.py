"""审计 idolMatrix 里艺人/组合的「竖版海报」(avatar_path) 覆盖率与分辨率。

用法: python audit_posters.py [--json out.json]
输出: 缺图清单 + 低分辨率清单（按短边升序）。
"""
from __future__ import annotations

import json
import sys
from typing import Any

from im_api import Api

sys.stdout.reconfigure(encoding="utf-8")

# 低分辨率门槛：短边 < LOW_SIDE 视为「海报分辨率过低」
LOW_SIDE = 800


def image_dims(api: Api, kind: str, entity_id: int, rel_path: str | None) -> tuple[int, int] | None:
    """从候选图列表里找到当前主图，返回 (w, h)。"""
    if not rel_path:
        return None
    try:
        data = api.get(f"/api/uploads/images/{kind}/{entity_id}")
    except RuntimeError as e:
        print(f"  ! images {kind}/{entity_id}: {e}")
        return None
    for row in data.get("items") or []:
        if row.get("rel_path") == rel_path:
            w, h = row.get("width"), row.get("height")
            if w and h:
                return int(w), int(h)
            return None
    return None


def collect(api: Api, kind: str) -> list[dict[str, Any]]:
    rows = api.paged(f"/api/{kind}s")
    out = []
    for r in rows:
        dims = image_dims(api, kind, r["id"], r.get("avatar_path"))
        item = {
            "type": kind,
            "id": r["id"],
            "name": r.get("name"),
            "korean_name": r.get("korean_name"),
            "avatar_path": r.get("avatar_path"),
            "banner_path": r.get("banner_path"),
            "width": dims[0] if dims else None,
            "height": dims[1] if dims else None,
            "short_side": min(dims) if dims else 0,
        }
        out.append(item)
        print(f"  {kind} #{r['id']} {r.get('name')}: {item['width']}x{item['height']}")
    return out


def main() -> None:
    api = Api()
    print("== 艺人 ==")
    artists = collect(api, "artist")
    print("== 组合 ==")
    groups = collect(api, "group")
    items = artists + groups

    missing = [i for i in items if not i["avatar_path"]]
    unknown = [i for i in items if i["avatar_path"] and not i["short_side"]]
    low = [i for i in items if i["short_side"] and i["short_side"] < LOW_SIDE]
    ok = [i for i in items if i["short_side"] and i["short_side"] >= LOW_SIDE]

    print("\n==== 汇总 ====")
    print(f"总条目 {len(items)}：无海报 {len(missing)} / 尺寸未知 {len(unknown)} / 短边<{LOW_SIDE} {len(low)} / 达标 {len(ok)}")
    print("\n低分辨率（短边升序）:")
    for i in sorted(low, key=lambda x: x["short_side"]):
        print(f"  {i['type']}#{i['id']:<4} {i['name']:<22} {i['width']}x{i['height']}")

    payload = {
        "low_side_threshold": LOW_SIDE,
        "missing": missing,
        "unknown": unknown,
        "low": low,
        "ok": ok,
    }
    with open("poster_audit.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print("\n已写 poster_audit.json")


if __name__ == "__main__":
    main()
