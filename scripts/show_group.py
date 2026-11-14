"""查看 idolMatrix 中某个团体的成员与当前海报状态（练手/核对用）。"""
import sys

from im_api import Api

sys.stdout.reconfigure(encoding="utf-8")

api = Api()
groups = api.paged("/api/groups")
for g in groups:
    if (g.get("name") or "").casefold() == "rescene":
        print("group", g["id"], g["name"], g.get("avatar_path"), g.get("banner_path"))
        mem = api.get(f"/api/memberships?group_id={g['id']}&page_size=100")
        for m in mem.get("items") or []:
            a = api.get(f"/api/artists/{m['artist_id']}")
            print(
                f"  artist#{a['id']:<4} {a['name']:<12} {a.get('korean_name')} "
                f"birth={a.get('birth_date')} avatar={a.get('avatar_path')}"
            )
