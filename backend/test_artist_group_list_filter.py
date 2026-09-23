"""艺人/组合列表过滤与批量统计口径（v3.2.54 根治）验收测试。

与歌曲列表（v3.2.53）同款改造：
  - artists：video_count 从「SELECT 逐行相关 COUNT」改为「视频侧一次预聚合 IN + 批量 GROUP BY」
  - groups：linked/orphan 过滤改预聚合 IN；member_count 改批量 GROUP BY；
    company_ids 由 selectinload 预载（消除双 N+1）

锁定口径不变：
  ① 艺人「关联」= music_video_artists 命中（组合成员身份不算），排除软删视频
  ② 组合 linked = 有视频 **或** 有任一成员经历；orphan = 两者皆无
  ③ 展示用 member_count 只数 Active 成员且艺人未软删
  ④ video_count 排除软删视频；company_ids 正确

直调路由函数必须显式传全部 Query 参数（缺省是 Query 对象真值陷阱）。

只跑本文件：
    python test_artist_group_list_filter.py
"""
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="artist_group_list_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.api.artists import list_artists  # noqa: E402
from app.api.groups import list_groups  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.company import Company  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.membership import GroupMembership  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402

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


def mk_artist(name, **kw):
    a = Artist(name=name, **kw)
    db.add(a)
    db.flush()
    return a


def mk_group(name, **kw):
    g = Group(name=name, **kw)
    db.add(g)
    db.flush()
    return g


def mk_mv(name):
    mv = MusicVideo(
        name=name,
        video_type="MV",
        video_types=["MV"],
        ingestion_status="library",
    )
    db.add(mv)
    db.flush()
    return mv


def soft_delete(obj):
    obj.deleted_at = datetime.now(timezone.utc)
    db.flush()


# ===== 艺人夹具 =====
a_linked = mk_artist("Linked")        # 1 条活视频 → linked，video_count=1
a_multi = mk_artist("MultiVideos")    # 1 活 + 1 软删 → linked，video_count=1
a_deadonly = mk_artist("DeadOnly")    # 只有软删视频 → orphan，video_count=0
a_orphan = mk_artist("PureOrphan")    # 无视频 → orphan

mv1 = mk_mv("MV_A1")
mv1.artists.append(a_linked)
mv2 = mk_mv("MV_A2")
mv2.artists.append(a_multi)
mv3 = mk_mv("MV_A3")
mv3.artists.append(a_multi)
soft_delete(mv3)
db.flush()

# ===== 组合夹具 =====
g_video = mk_group("VideoGroup")      # 有视频无成员 → linked
g_member = mk_group("MemberGroup")    # 无视频有成员 → linked
g_both = mk_group("BothGroup")        # 有视频有成员 → linked
g_orphan = mk_group("OrphanGroup")    # 无视频无成员 → orphan

mv4 = mk_mv("MV_G1")
mv4.groups.append(g_video)
mv5 = mk_mv("MV_G2")
mv5.groups.append(g_both)
m_both = GroupMembership(group_id=g_both.id, artist_id=a_linked.id, status="Active")
m_former = GroupMembership(group_id=g_member.id, artist_id=a_multi.id, status="Former")
db.add_all([m_both, m_former])

# 组合-公司关系（company_ids 预载路径）
comp = Company(name="STARSHIP")
db.add(comp)
db.flush()
from app.models.company import GroupCompanyRelation  # noqa: E402

db.add(GroupCompanyRelation(group_id=g_video.id, company_id=comp.id))
db.commit()


def q_artists(**kw):
    class P:
        page = 1
        page_size = 200
        offset = 0
        limit = 200

    kw.setdefault("q", None)
    kw.setdefault("sort", None)
    kw.setdefault("only_with_videos", False)
    kw.setdefault("filter", "all")
    kw["db"] = db
    kw["pagination"] = P()
    return list_artists(**kw)


def q_groups(**kw):
    class P:
        page = 1
        page_size = 200
        offset = 0
        limit = 200

    kw.setdefault("q", None)
    kw.setdefault("group_type", None)
    kw.setdefault("sort", None)
    kw.setdefault("filter", "all")
    kw["db"] = db
    kw["pagination"] = P()
    return list_groups(**kw)


def anames(res):
    return sorted(i.name for i in res.items)


def gnames(res):
    return sorted(i.name for i in res.items)


# ===== 艺人：filter=all（video_count 批量路径） =====
ra = q_artists()
by = {i.name: i for i in ra.items}
check("艺人 all：4 人全在", anames(ra) == ["DeadOnly", "Linked", "MultiVideos", "PureOrphan"], anames(ra))
check("Linked video_count=1", by["Linked"].video_count == 1, by["Linked"].video_count)
check("MultiVideos video_count=1（软删不算）", by["MultiVideos"].video_count == 1, by["MultiVideos"].video_count)
check("DeadOnly video_count=0", by["DeadOnly"].video_count == 0, by["DeadOnly"].video_count)
check("PureOrphan video_count=0", by["PureOrphan"].video_count == 0, by["PureOrphan"].video_count)

# ===== 艺人：linked / orphan =====
check("艺人 linked = Linked+MultiVideos", anames(q_artists(filter="linked")) == ["Linked", "MultiVideos"],
      anames(q_artists(filter="linked")))
check("艺人 linked 与 only_with_videos 一致",
      anames(q_artists(filter="linked")) == anames(q_artists(only_with_videos=True)))
check("艺人 orphan = DeadOnly+PureOrphan", anames(q_artists(filter="orphan")) == ["DeadOnly", "PureOrphan"],
      anames(q_artists(filter="orphan")))

# ===== 组合：filter=all =====
rg = q_groups()
gb = {i.name: i for i in rg.items}
check("组合 all：4 组全在", gnames(rg) == ["BothGroup", "MemberGroup", "OrphanGroup", "VideoGroup"], gnames(rg))
check("VideoGroup linked（有视频无成员）", gb["VideoGroup"].member_count == 0, gb["VideoGroup"].member_count)
check("MemberGroup linked 但 member_count=0（Former 不计）", gb["MemberGroup"].member_count == 0,
      gb["MemberGroup"].member_count)
check("BothGroup member_count=1（Active）", gb["BothGroup"].member_count == 1, gb["BothGroup"].member_count)
check("VideoGroup company_ids 预载正确", gb["VideoGroup"].company_ids == [comp.id],
      gb["VideoGroup"].company_ids)
check("OrphanGroup company_ids 空列表", gb["OrphanGroup"].company_ids == [], gb["OrphanGroup"].company_ids)

# ===== 组合：linked / orphan（有视频或任一成员经历） =====
check("组合 linked = 3 组", gnames(q_groups(filter="linked")) == ["BothGroup", "MemberGroup", "VideoGroup"],
      gnames(q_groups(filter="linked")))
check("组合 orphan = OrphanGroup", gnames(q_groups(filter="orphan")) == ["OrphanGroup"],
      gnames(q_groups(filter="orphan")))

# ===== 软删艺人退出所有口径 =====
soft_delete(a_linked)
db.commit()
ra2 = q_artists()
check("软删艺人从 all 消失", "Linked" not in anames(ra2), anames(ra2))
rg2 = q_groups()
check("软删艺人成员经历使 MemberGroup member_count 仍 0",
      {i.name: i for i in rg2.items}["MemberGroup"].member_count == 0)
check("软删艺人后组合 linked 口径不变（成员经历仍在）",
      "MemberGroup" in gnames(q_groups(filter="linked")), gnames(q_groups(filter="linked")))

db.close()
print(f"\n== artist_group_list_filter: PASS={PASS} FAIL={FAIL} ==")
sys.exit(1 if FAIL else 0)
