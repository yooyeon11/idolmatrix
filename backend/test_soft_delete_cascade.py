"""软删除级联 + 悬空残留清理。

验证：
1. soft_delete_group 拆除 memberships / 公司关系 / 小分队 parent
2. soft_delete_artist 拆除 memberships / 公司关系 / MV subject
3. purge_dangling_edges 清理历史残留后，体检悬空检查归零
4. restore 只恢复实体，关系不会自动回来
"""
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="soft_delete_cascade_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from sqlalchemy import func, select  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.company import (  # noqa: E402
    ArtistCompanyRelation,
    Company,
    GroupCompanyRelation,
)
from app.models.group import Group  # noqa: E402
from app.models.membership import GroupMembership  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.services.data_health import run_health_report  # noqa: E402
from app.services.soft_delete_service import (  # noqa: E402
    purge_dangling_edges,
    soft_delete_artist,
    soft_delete_company,
    soft_delete_group,
)

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

print("== 1. soft_delete_group 拆除边 ==")
g = Group(name="Cascade Group", debut_date=date(2020, 1, 1))
sub = Group(name="Cascade Sub", debut_date=date(2021, 1, 1))
a = Artist(name="Cascade Member", birth_date=date(2000, 1, 1))
c = Company(name="Cascade Co")
db.add_all([g, sub, a, c])
db.flush()
sub.parent_group_id = g.id
db.add(
    GroupMembership(
        group_id=g.id, artist_id=a.id, join_date=date(2020, 2, 1), status="Active"
    )
)
db.add(GroupCompanyRelation(group_id=g.id, company_id=c.id, status="Active"))
db.commit()
db.refresh(g)
db.refresh(sub)
db.refresh(a)

counts = soft_delete_group(db, g)
db.commit()
check("组合已软删", g.deleted_at is not None)
check("成员关系已删", counts["memberships_deleted"] == 1)
check("公司关系已删", counts["group_company_relations_deleted"] == 1)
check("小分队上级已清空", counts["subunit_parents_cleared"] == 1)
db.refresh(sub)
check("小分队仍存活且 parent 为空", sub.deleted_at is None and sub.parent_group_id is None)
check(
    "DB 无该组合成员关系",
    db.scalar(select(func.count()).select_from(GroupMembership).where(GroupMembership.group_id == g.id)) == 0,
)

print("== 2. soft_delete_artist 拆除边 ==")
g2 = Group(name="Artist Host", debut_date=date(2019, 1, 1))
a2 = Artist(name="Solo Star", birth_date=date(1999, 5, 5))
c2 = Company(name="Solo Co")
db.add_all([g2, a2, c2])
db.flush()
db.add(GroupMembership(group_id=g2.id, artist_id=a2.id, status="Active"))
db.add(ArtistCompanyRelation(artist_id=a2.id, company_id=c2.id, status="Active"))
mv = MusicVideo(name="Fancam 1", file_hash="hash-fancam-1", video_type="PersonalFancam", subject_artist_id=a2.id)
db.add(mv)
db.commit()
db.refresh(a2)
db.refresh(mv)

counts = soft_delete_artist(db, a2)
db.commit()
check("艺人已软删", a2.deleted_at is not None)
check("艺人成员关系已删", counts["memberships_deleted"] == 1)
check("艺人公司关系已删", counts["artist_company_relations_deleted"] == 1)
check("MV subject 已清空", counts["mv_subjects_cleared"] == 1)
db.refresh(mv)
check("MV 仍存活且 subject 为空", mv.deleted_at is None and mv.subject_artist_id is None)

print("== 3. soft_delete_company 拆除边 ==")
c3 = Company(name="Doomed Co")
a3 = Artist(name="Still Alive", birth_date=date(2001, 1, 1))
g3 = Group(name="Still Alive G", debut_date=date(2018, 1, 1))
db.add_all([c3, a3, g3])
db.flush()
db.add(ArtistCompanyRelation(artist_id=a3.id, company_id=c3.id, status="Active"))
db.add(GroupCompanyRelation(group_id=g3.id, company_id=c3.id, status="Active"))
db.commit()
db.refresh(c3)
counts = soft_delete_company(db, c3)
db.commit()
check("公司已软删", c3.deleted_at is not None)
check("公司艺人关系已删", counts["artist_company_relations_deleted"] == 1)
check("公司组合关系已删", counts["group_company_relations_deleted"] == 1)

print("== 4. purge 清理历史残留 + 体检归零 ==")
# 造「旧行为」残留：只 soft_delete 实体，不拆边
ghost_a = Artist(name="Ghost A")
ghost_g = Group(name="Ghost G")
live_g = Group(name="Live Host", debut_date=date(2017, 1, 1))
live_a = Artist(name="Live A", birth_date=date(1998, 1, 1))
ghost_c = Company(name="Ghost Co")
db.add_all([ghost_a, ghost_g, live_g, live_a, ghost_c])
db.flush()
db.add(GroupMembership(group_id=live_g.id, artist_id=ghost_a.id, status="Active"))
db.add(GroupMembership(group_id=ghost_g.id, artist_id=live_a.id, status="Active"))
db.add(ArtistCompanyRelation(artist_id=live_a.id, company_id=ghost_c.id, status="Active"))
db.add(GroupCompanyRelation(group_id=live_g.id, company_id=ghost_c.id, status="Active"))
subunit = Group(name="Live Sub", debut_date=date(2018, 6, 1), parent_group_id=ghost_g.id)
db.add(subunit)
mv2 = MusicVideo(name="Ghost Subject MV", file_hash="hash-ghost-mv", video_type="PersonalFancam", subject_artist_id=ghost_a.id)
db.add(mv2)
db.flush()
# 旧行为：只写 tombstone
ghost_a.soft_delete()
ghost_g.soft_delete()
ghost_c.soft_delete()
db.commit()

report_before = run_health_report(db)
dangling_before = [
    i
    for i in report_before["issues"]
    if i["check"]
    in ("membership_dangling", "mv_subject_dangling", "subunit_parent_dangling")
]
check("purge 前有悬空问题", len(dangling_before) >= 3, f"n={len(dangling_before)}")

purge_counts = purge_dangling_edges(db)
db.commit()
check("purge 删除成员关系", purge_counts["memberships_deleted"] >= 2)
check("purge 清空 MV subject", purge_counts["mv_subjects_cleared"] >= 1)
check("purge 清空小分队上级", purge_counts["subunit_parents_cleared"] >= 1)
check(
    "purge 删除公司关系",
    purge_counts["artist_company_relations_deleted"]
    + purge_counts["group_company_relations_deleted"]
    >= 2,
)

# 幂等
again = purge_dangling_edges(db)
db.commit()
check("purge 幂等全 0", sum(again.values()) == 0, again)

report_after = run_health_report(db)
dangling_after = [
    i
    for i in report_after["issues"]
    if i["check"]
    in ("membership_dangling", "mv_subject_dangling", "subunit_parent_dangling")
]
check("purge 后悬空检查归零", len(dangling_after) == 0, dangling_after)

print("== 5. restore 不重建关系 ==")
ghost_a.restore()
db.commit()
check("艺人已恢复", ghost_a.deleted_at is None)
check(
    "恢复后仍无成员关系",
    db.scalar(select(func.count()).select_from(GroupMembership).where(GroupMembership.artist_id == ghost_a.id)) == 0,
)
db.refresh(mv2)
check("恢复后 MV subject 仍为空", mv2.subject_artist_id is None)

print()
print(f"RESULT  PASS={PASS}  FAIL={FAIL}")
sys.exit(0 if FAIL == 0 else 1)
