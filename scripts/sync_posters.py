"""把 kpopping 的成员/组合海报批量补进 idolMatrix（仅补缺 + 升级低分辨率）。

用法:
  python sync_posters.py --plan                    # 只匹配，输出 poster_plan.json
  python sync_posters.py --plan --only-group RESCENE
  python sync_posters.py --apply --only-group RESCENE
  python sync_posters.py --apply                   # 全量

规则（逐实体）:
  * 无 avatar_path                    -> 补
  * 有 avatar_path 且短边 < LOW_SIDE  -> 仅当新图短边更大时替换
  * 已有高清图                         -> 跳过
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from PIL import Image

from im_api import Api
from kpopping import Kpopping, norm

sys.stdout.reconfigure(encoding="utf-8")

LOW_SIDE = 800
JPEG_QUALITY = 90
HERE = Path(__file__).parent
SEARCH_CACHE = HERE / "kpopping_search_cache.json"


# ---------------- 缓存 ----------------
def load_search_cache() -> dict[str, Any]:
    if SEARCH_CACHE.exists():
        return json.loads(SEARCH_CACHE.read_text(encoding="utf-8"))
    return {}


def save_search_cache(cache: dict[str, Any]) -> None:
    SEARCH_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


# ---------------- 匹配 ----------------
def match_member(artist: dict, members: list[dict]) -> Optional[dict]:
    keys = {norm(artist.get(k)) for k in ("name", "stage_name", "english_name", "korean_name")}
    keys.discard("")
    best: tuple[int, dict] | None = None
    for m in members:
        score = 0
        if keys & {norm(m.get("name")), norm(m.get("koreanName"))}:
            score += 10
        if artist.get("birth_date") and artist["birth_date"] == m.get("birthday"):
            score += 5
        if artist.get("korean_name") and artist["korean_name"] == m.get("koreanName"):
            score += 5
        if score and (best is None or score > best[0]):
            best = (score, m)
    return best[1] if best and best[0] >= 10 else None


def prepare_image(raw: bytes) -> tuple[bytes, tuple[int, int]]:
    im = Image.open(io.BytesIO(raw))
    im.load()
    w, h = im.size
    if im.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        rgba = im.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return out.getvalue(), (w, h)


# ---------------- 主流程 ----------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only-group", default=None)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--include-groups", action="store_true", help="同时处理组合头像")
    ap.add_argument("--from-plan", default=None, help="直接执行已有计划 JSON（不再抓名录/搜索）")
    ap.add_argument("--no-idol-search", action="store_true",
                    help="跳过按艺人名单搜 kpopping（无组合名册时直接记为未匹配）")
    args = ap.parse_args()
    if not (args.plan or args.apply):
        ap.error("需要 --plan 或 --apply")
    scope = norm(args.only_group) if args.only_group else None

    api = Api()
    kp = Kpopping()
    cache = load_search_cache()

    if args.from_plan:
        plan_data = json.loads(Path(args.from_plan).read_text(encoding="utf-8"))
        jobs = plan_data.get("artists", []) + plan_data.get("groups", [])
        print(f"从计划加载 {len(jobs)} 条任务")
        apply_jobs(jobs, api, kp, args.workers)
        return

    def kp_search(q: str) -> dict:
        key = q.casefold().strip()
        if key in cache:
            return cache[key]
        data = kp.search(q)
        cache[key] = data
        return data

    audit = json.loads((HERE / "poster_audit.json").read_text(encoding="utf-8"))
    todo_artists = {i["id"]: i for i in audit["missing"] + audit["low"] if i["type"] == "artist"}
    todo_groups = {i["id"]: i for i in audit["missing"] + audit["low"] if i["type"] == "group"}

    groups = api.paged("/api/groups")
    gname = {g["id"]: g["name"] for g in groups}
    memberships = api.paged("/api/memberships")
    artist_groups: dict[int, int] = {}
    group_members_db: dict[int, list[int]] = {}
    for m in memberships:
        artist_groups.setdefault(m["artist_id"], m["group_id"])
        group_members_db.setdefault(m["group_id"], []).append(m["artist_id"])

    # 作用域过滤
    def in_scope_artist(aid: int) -> bool:
        if not scope:
            return True
        gid = artist_groups.get(aid)
        return bool(gid and scope in norm(gname.get(gid)))

    scoped = {aid: it for aid, it in todo_artists.items() if in_scope_artist(aid)}
    print(f"待补艺人(全库) {len(todo_artists)}；本次作用域 {len(scoped)}")

    need_groups = sorted({artist_groups[a] for a in scoped if a in artist_groups})
    print(f"涉及组合 {len(need_groups)}")

    # 解析组合 -> kpopping slug + 成员表
    gmembers: dict[int, list[dict]] = {}
    gslugs: dict[int, str] = {}
    gimages: dict[int, str] = {}

    # 先用名录页 slug 索引（零额外请求），未命中再走 search
    INDEX_FILE = HERE / "kpopping_slug_index.json"
    slug_index: dict[str, str] = {}
    if INDEX_FILE.exists():
        slug_index = {int(k): v for k, v in json.loads(INDEX_FILE.read_text(encoding="utf-8")).items()}

    def resolve(gid: int):
        name = gname.get(gid) or ""
        slug = slug_index.get(gid)
        if not slug:
            # 名录页没有：先按名字猜 slug（不消耗 search 配额），再退回 search
            try:
                slug = kp.try_group_slug(name)
            except Exception:  # noqa: BLE001
                slug = None
            if not slug:
                try:
                    data = kp_search(name)
                except Exception as e:  # noqa: BLE001
                    return gid, None, [], f"unresolved ({e})"
                cands = data.get("groups") or []
                hit = next((c for c in cands if norm(c.get("name")) == norm(name)), None)
                if hit is None:
                    return gid, None, [], "no group hit"
                slug = hit["slug"]
        try:
            ms = kp.group_members(slug)
        except Exception as e:  # noqa: BLE001
            return gid, {"slug": slug}, [], f"members err {e}"
        if not ms:
            return gid, {"slug": slug}, [], "empty roster"
        return gid, {"slug": slug, "image": ""}, ms, "ok"

    print("解析 kpopping 组合…")
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(resolve, gid): gid for gid in need_groups}
        for fut in futs:
            try:
                gid, hit, ms, status = fut.result()
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ {gname.get(futs[fut])}: EXC {type(e).__name__} {e}")
                continue
            if hit and ms:
                gslugs[gid] = hit["slug"]
                gmembers[gid] = ms
                gimages[gid] = hit.get("image") or ""
                print(f"  ✓ {gname.get(gid)} -> {hit['slug']} ({len(ms)} members)")
            else:
                print(f"  ✗ {gname.get(gid)}: {status}")

    # 匹配
    plan: list[dict[str, Any]] = []
    unmatched: list[dict] = []
    pending_search: list[int] = []

    for aid, item in scoped.items():
        gid = artist_groups.get(aid)
        member = match_member(item, gmembers.get(gid, [])) if gid in gmembers else None
        if member is None:
            if gid in gmembers:
                # 组合名册已解析但没这个人：多半是库内多建/别名差异，不贸然单搜
                unmatched.append({"id": aid, "name": item["name"], "reason": "not in kpopping roster",
                                  "group": gname.get(gid)})
            else:
                pending_search.append(aid)
            continue
        plan.append(
            {
                "kind": "artist",
                "artist_id": aid,
                "artist_name": item["name"],
                "group": gname.get(gid),
                "kpopping_name": member.get("name"),
                "via": "group-members",
                "image": member.get("image"),
                "db_w": item["width"],
                "db_h": item["height"],
            }
        )

    print(f"经组合成员表匹配 {len(plan)}；剩余 {len(pending_search)} 个走 idol 搜索")

    if args.no_idol_search:
        for aid in pending_search:
            unmatched.append({"id": aid, "name": scoped[aid]["name"],
                              "reason": "group not on kpopping (skipped idol search)",
                              "group": gname.get(artist_groups.get(aid))})
        pending_search = []

    def search_artist(aid: int):
        item = scoped[aid]
        gid = artist_groups.get(aid)
        try:
            data = kp_search(item["name"])
        except Exception as e:  # noqa: BLE001
            return aid, None, f"err {e}"
        idols = data.get("idols") or []
        if not idols:
            return aid, None, "no idol"
        want_group = (gname.get(gid) or "").casefold() if gid else None
        pick = None
        if want_group:
            same = [i for i in idols if (i.get("groupName") or "").casefold() == want_group]
            if same:
                pick = same[0]
        if pick is None:
            exact = [i for i in idols if norm(i.get("name")) == norm(item["name"])]
            if exact:
                pick = exact[0]
        if pick is None:
            return aid, None, f"ambiguous ({len(idols)})"
        # 同名防串档：能取到对方生日则必须与库内一致
        if item.get("birth_date"):
            bd = kp.idol_birthday(pick["slug"])
            if bd and bd != item["birth_date"]:
                return aid, None, f"birthday mismatch {bd} != {item['birth_date']}"
        return aid, pick, "ok"

    with ThreadPoolExecutor(args.workers) as ex:
        for aid, idol, status in ex.map(search_artist, pending_search):
            item = scoped[aid]
            if idol:
                plan.append(
                    {
                        "kind": "artist",
                        "artist_id": aid,
                        "artist_name": item["name"],
                        "group": gname.get(artist_groups.get(aid)),
                        "kpopping_name": idol.get("name"),
                        "via": "idol-search",
                        "image": idol.get("image"),
                        "db_w": item["width"],
                        "db_h": item["height"],
                    }
                )
            else:
                unmatched.append({"id": aid, "name": item["name"], "reason": status})

    # 组合头像
    group_plan: list[dict[str, Any]] = []
    if args.include_groups:
        for gid, item in todo_groups.items():
            if scope and scope not in norm(item["name"]):
                continue
            if gid in gimages and gimages[gid]:
                group_plan.append(
                    {
                        "kind": "group",
                        "group_id": gid,
                        "name": item["name"],
                        "image": gimages[gid],
                        "db_w": item["width"],
                        "db_h": item["height"],
                    }
                )
            else:
                # 未在艺人解析里出现，单独搜
                try:
                    data = kp_search(item["name"])
                except Exception as e:  # noqa: BLE001
                    unmatched.append({"id": gid, "name": item["name"], "reason": f"group err {e}"})
                    continue
                cands = data.get("groups") or []
                hit = next((c for c in cands if norm(c.get("name")) == norm(item["name"])), None)
                if hit and hit.get("image"):
                    group_plan.append(
                        {
                            "kind": "group",
                            "group_id": gid,
                            "name": item["name"],
                            "image": hit["image"],
                            "db_w": item["width"],
                            "db_h": item["height"],
                        }
                    )
                else:
                    unmatched.append({"id": gid, "name": item["name"], "reason": "no group image"})

    print(f"\n艺人匹配 {len(plan)}；组合匹配 {len(group_plan)}；未匹配 {len(unmatched)}")
    for p in plan[:400]:
        print(f"  artist#{p['artist_id']:<4} {p['artist_name']:<22} <- {p['kpopping_name']:<18} [{p['via']}]")
    for p in group_plan:
        print(f"  group#{p['group_id']:<4} {p['name']:<22} <- {p['image'].split('?')[0]}")

    save_search_cache(cache)
    out = {"artists": plan, "groups": group_plan, "unmatched": unmatched}
    (HERE / "poster_plan.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写 poster_plan.json")

    if not args.apply:
        return

    apply_jobs(plan + group_plan, api, kp, args.workers)


def apply_jobs(jobs: list[dict], api: Api, kp: Kpopping, workers: int) -> list[dict]:
    """下载 -> 转 JPEG -> 上传头像；返回每条结果。"""

    def process(job: dict) -> dict:
        kind = job["kind"]
        url = job["image"]
        eid = job["artist_id"] if kind == "artist" else job["group_id"]
        try:
            raw = kp.cached_get(url, timeout=150)
            data, (w, h) = prepare_image(raw)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "kind": kind, "id": eid, "err": f"download {e}"}
        db_short = min(job.get("db_w") or 0, job.get("db_h") or 0)
        new_short = min(w, h)
        if db_short and new_short <= db_short:
            return {"ok": False, "kind": kind, "id": eid,
                    "err": f"not better {w}x{h} vs {job.get('db_w')}x{job.get('db_h')}"}
        r = api.call(
            "POST",
            f"/api/uploads/avatar/{kind}/{eid}",
            files={"file": ("a.jpg", data, "image/jpeg")},
            timeout=180,
        )
        if not r.ok:
            return {"ok": False, "kind": kind, "id": eid, "err": f"upload {r.status_code} {r.text[:160]}"}
        return {"ok": True, "kind": kind, "id": eid,
                "name": job.get("artist_name") or job.get("name"),
                "w": w, "h": h, "kb": len(data) // 1024, "resp": r.json()}

    print("\n开始下载/上传…")
    results: list[dict] = []
    with ThreadPoolExecutor(workers) as ex:
        for res in ex.map(process, jobs):
            results.append(res)
            print("  ", res, flush=True)

    ok = [r for r in results if r.get("ok")]
    print(f"\n完成：成功 {len(ok)} / {len(results)}")
    (HERE / "poster_sync_result.json").write_text(
        json.dumps({"results": results}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return results


if __name__ == "__main__":
    main()
