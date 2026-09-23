"""db_views（关系化列表）测试：行数据、成员轨迹、完整度、问题归因。"""
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="db_views_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.album import Album, AlbumTrack  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.company import Company, GroupCompanyRelation  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.membership import GroupMembership  # noqa: E402
from app.models.song import Song  # noqa: E402
from app.services.data_health import invalidate_health_cache  # noqa: E402
from app.services.db_views import build_group_rows, build_group_workspace  # noqa: E402

init_db()
Base.metadata.create_all(bind=engine)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}  {detail}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


db = SessionLocal()

# ===== 造数据 =====
g_full = Group(
    name="Full Group",
    debut_date=date(2022, 10, 13),
    group_type="Girl Group",
    description="测试组合",
    avatar_path="avatars/groups/1.jpg",
)
g_bare = Group(name="Bare Group")  # 什么都没有
a1 = Artist(name="Member One", birth_date=date(2000, 1, 1))
a2 = Artist(name="Member Two", birth_date=date(2001, 1, 1))
a_former = Artist(name="Former One", birth_date=date(1999, 1, 1))
c1 = Company(name="Label One")
db.add_all([g_full, g_bare, a1, a2, a_former, c1])
db.flush()
db.add_all(
    [
        GroupMembership(
            group_id=g_full.id, artist_id=a1.id,
            join_date=date(2022, 7, 8), status="Active", positions=["Leader"],
        ),
        GroupMembership(
            group_id=g_full.id, artist_id=a2.id,
            join_date=date(2022, 7, 8), status="Active",
        ),
        GroupMembership(
            group_id=g_full.id, artist_id=a_former.id,
            join_date=date(2022, 1, 1), leave_date=date(2023, 1, 1), status="Former",
        ),
        GroupCompanyRelation(
            group_id=g_full.id, company_id=c1.id, role="厂牌",
            start_date=date(2022, 1, 1), status="Active",
        ),
    ]
)
alb = Album(name="Test Album", release_date=date(2023, 1, 1), release_artist_type="group", release_artist_id=g_full.id)
db.add(alb)
db.flush()
s1 = Song(name="Test Song", release_artist_type="group", release_artist_id=g_full.id)
db.add(s1)
db.flush()
from app.models.album import AlbumTrack  # noqa: E402

db.add(AlbumTrack(album_id=alb.id, song_id=s1.id, track_number=1))
db.commit()
invalidate_health_cache()

result = build_group_rows(db)
rows = {r["uid"]: r for r in result["items"]}
full = rows[g_full.uid]
bare = rows[g_bare.uid]

print("== 1. 行数据结构 ==")
check("两组都在列表中", result["total"] == 2)
check("问题多的组合排前面", result["items"][0]["uid"] == g_bare.uid)
check("full 行成员数 2+1", full["members_active"] == 2 and full["members_former"] == 1)
check("成员轨迹含字段", all(
    k in full["members"][0] for k in ("join_date", "status", "positions", "artist_uid")
))
check("成员按加入时间排序", full["members"][0]["name"] == "Former One")
check("公司关系带角色与起止", full["companies"][0]["role"] == "厂牌" and full["companies"][0]["start_date"] == "2022-01-01")
check("作品计数正确", full["works"] == {"albums": 1, "songs": 1, "videos": 0}, full["works"])
check("bare 组合零成员零作品", bare["members_active"] == 0 and sum(bare["works"].values()) == 0)

print("== 2. 完整度 ==")
check("full 组合完整度 7/8（缺作品关联→有）", full["completeness"] == 100, full["completeness"])
# full: 出道✓ 类型✓ 描述✓ 头像✓ 成员✓ 公司✓ 作品✓ = 100
check("bare 组合完整度低", bare["completeness"] <= 25, bare["completeness"])
check("bare 缺失清单可读", "出道日期" in bare["completeness_missing"] and "成员" in bare["completeness_missing"])
check("checklist 暴露给前端", len(result["checklist"]) == 7)

print("== 3. 问题归因 ==")
# bare 组合应吃到: group_no_members / missing_dates(出道) / company? (公司无关系是公司侧,不算组合)
check("bare 组合有 warning 问题", bare["issues"]["warning"] >= 2, bare["issues"])
check("full 组合无 error 问题", full["issues"]["error"] == 0, full["issues"])

print("== 4. 悬空关系标记 ==")
a_ghost = Artist(name="Ghost", birth_date=date(1998, 1, 1))
db.add(a_ghost)
db.flush()
db.add(GroupMembership(group_id=g_full.id, artist_id=a_ghost.id, join_date=date(2023, 1, 1), status="Active"))
db.commit()
invalidate_health_cache()
import datetime  # noqa: E402

a_ghost.deleted_at = datetime.datetime.utcnow()
db.commit()
invalidate_health_cache()
result2 = build_group_rows(db)
rows2 = {r["uid"]: r for r in result2["items"]}
ghost_rows = [m for m in rows2[g_full.uid]["members"] if m["name"] == "Ghost"]
check("悬空成员带 is_dangling 标记", ghost_rows and ghost_rows[0]["is_dangling"] is True)
check("悬空成员不计入在籍数", rows2[g_full.uid]["members_active"] == 2, rows2[g_full.uid]["members_active"])


# ============ 工作台 ============
print("== 5. 工作台 ==")
ws = build_group_workspace(db, g_full.uid)
check("工作台返回基本信息", ws is not None and ws["group"]["name"] == "Full Group")
check("工作台 row 与列表一致", ws["row"] is not None and ws["row"]["uid"] == g_full.uid)
check("外部链接透传", isinstance(ws["group"]["external_links"], list))
# bare 组合工作台应吃到 missing_dates(group) 问题
ws_bare = build_group_workspace(db, g_bare.uid)
check("bare 工作台有问题清单", ws_bare["issue_counts"]["warning"] >= 1, ws_bare["issue_counts"])
check("问题条目可定位（含 check 字段）", all("check" in i for i in ws_bare["issues"]))
check("不存在 uid 返回 None", build_group_workspace(db, "nonexistent-uid") is None)

# 手动修复 bare 的出道日期后问题消失
g_bare.debut_date = date(2020, 5, 5)
db.commit()
invalidate_health_cache()
ws_bare2 = build_group_workspace(db, g_bare.uid)
check("修复后出道日问题消失", not any(
    "出道日期" in i["title"] for i in ws_bare2["issues"]
), ws_bare2["issue_counts"])

db.close()
print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
sys.exit(1 if FAIL else 0)
