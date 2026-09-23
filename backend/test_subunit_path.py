"""小分队作品「磁盘归母队 + 数据层留分队」（v3.2.42）验收测试。

规则（用户定稿）：入库目录的**组合主体段一律取根母队** —— 挂 EVOLution /
LOVElution 等小分队的视频也落 `tripleS/`，同一母队不会散成多个顶层目录；
多级小分队（分队的再分队）一路上溯到最顶层。

分队归属本身完整保留在数据层（mv.groups 照旧挂小分队），
分队维度的检索由 group_tree.expand_group_ids 聚合负责 —— 两件事互不冲突。

只跑本文件：
    python test_subunit_path.py
"""
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="subunit_path_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.models.song import Song  # noqa: E402
from app.services.group_tree import expand_group_ids, root_group  # noqa: E402
from app.services.library_service import (  # noqa: E402
    _bili_rename,
    auto_organize_rel,
    plan_reorganize,
    suggest_relocate_path,
)

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


def posix(p):
    return p.as_posix() if p is not None else "None"


# ===== 夹具：母队 / 小分队 / 二级小分队 / 软删母队 / 环 =====
db = SessionLocal()


def mk_group(name, chinese=None, parent=None, deleted=False):
    g = Group(name=name, chinese_name=chinese)
    if parent is not None:
        g.parent_group_id = parent.id
    if deleted:
        g.deleted_at = datetime(2024, 1, 1)
    db.add(g)
    db.flush()
    return g


g_root = mk_group("tripleS", "TripleS")          # 母队（中文名优先 → 路径段 TripleS）
g_sub = mk_group("EVOLution", parent=g_root)     # 小分队
g_sub2 = mk_group("LOVElution", parent=g_root)   # 兄弟小分队
g_deep = mk_group("DeepSub", parent=g_sub)       # 二级小分队（分队的再分队）
g_self = mk_group("SelfLoop")                    # 自环
g_self.parent_group_id = g_self.id
db.flush()
g_a = mk_group("LoopA")                          # 互环 A <-> B
g_b = mk_group("LoopB", parent=g_a)
g_a.parent_group_id = g_b.id
db.flush()
g_dead = mk_group("DeadParent", deleted=True)    # 已软删的母队
g_orphan = mk_group("OrphanSub", parent=g_dead)

a_member = Artist(name="Yooyeon", stage_name="Yooyeon")
db.add(a_member)
db.commit()

print("== 1. root_group：沿 parent_group_id 上溯到顶层母队 ==")
check("母队自身不迁移", root_group(db, g_root).id == g_root.id)
check("小分队 → 母队", root_group(db, g_sub).id == g_root.id, root_group(db, g_sub).name)
check("兄弟小分队 → 同一母队", root_group(db, g_sub2).id == g_root.id)
check("二级小分队 → 顶层母队", root_group(db, g_deep).id == g_root.id, root_group(db, g_deep).name)
check("None → None", root_group(db, None) is None)
check("无 Session 时不上溯", root_group(None, g_sub).id == g_sub.id)
check("母队已软删 → 停在当前层", root_group(db, g_orphan).id == g_orphan.id)
check("自环不死循环", root_group(db, g_self).id == g_self.id)
check("互环不死循环", root_group(db, g_a).id in (g_a.id, g_b.id), root_group(db, g_a).name)


# ===== 路径规则（走 Session，上溯生效）=====
def plan(video_types, *, group=None, artist=None, songs=(), is_short=False, is_solo=False,
         perf=None, name="clip"):
    mv = MusicVideo(
        name=name,
        video_type=video_types[0],
        video_types=list(video_types),
        is_short=is_short,
        is_solo=is_solo,
        performance_date=perf,
    )
    if group is not None:
        mv.groups.append(group)
    if artist is not None:
        mv.artists.append(artist)
    for s in songs:
        mv.songs.append(Song(name=s))
    db.add(mv)
    db.commit()
    db.refresh(mv)
    return auto_organize_rel(mv, db=db)


print("== 2. 常规路径：组合主体段取母队 ==")
p = plan(["PerformanceVideo"], group=g_sub, songs=["Girls Never Die"], name="mv1")
check("小分队 Live → 母队/歌曲/Live", posix(p) == "TripleS/Girls Never Die/Live", posix(p))

p = plan(["OfficialMV"], group=g_sub, songs=["Girls Never Die"], name="mv2")
check("小分队 MV → 母队/歌曲/MV", posix(p) == "TripleS/Girls Never Die/MV", posix(p))

p = plan(["PerformanceVideo"], group=g_deep, songs=["X"], name="mv3")
check("二级小分队 → 顶层母队", posix(p) == "TripleS/X/Live", posix(p))

p = plan(["PerformanceVideo"], group=g_sub2, songs=["X"], name="mv4")
check("兄弟小分队 → 同一母队目录", posix(p) == "TripleS/X/Live", posix(p))

p = plan(["PerformanceVideo"], group=g_root, songs=["X"], name="mv5")
check("母队自身路径不变（回归）", posix(p) == "TripleS/X/Live", posix(p))

p = plan(["PerformanceVideo"], group=g_sub, artist=a_member, songs=["X"], name="mv6")
check("成员直拍 组合段也归母队", posix(p) == "TripleS/X/Yooyeon", posix(p))

p = plan(["PerformanceVideo"], group=g_sub, name="mv7")
check("无歌曲段 组合直拍 → 母队/Live", posix(p) == "TripleS/Live", posix(p))


print("== 3. 个人口径四条（短视频 / 串烧 / 翻唱 / 非表演）也取母队 ==")
p = plan(["ShortVideo"], group=g_sub, is_short=True, name="st1")
check("短视频 → 短视频/母队", posix(p) == "短视频/TripleS", posix(p))

p = plan(["PerformanceVideo"], group=g_sub, songs=["A", "B"], perf=date(2024, 5, 1), name="st2")
check("串烧 → 串烧/母队/240501", posix(p) == "串烧/TripleS/240501", posix(p))

p = plan(["CoverStage"], group=g_sub, songs=["Gee"], name="st3")
check("翻唱 → 翻唱/母队/歌曲", posix(p) == "翻唱/TripleS/Gee", posix(p))

p = plan(["Other"], group=g_sub, name="st4")
check("非表演 → 非表演视频/母队", posix(p) == "非表演视频/TripleS", posix(p))

p = plan(["Teaser"], group=g_sub2, name="st5")
check("预告 → 非表演视频/母队", posix(p) == "非表演视频/TripleS", posix(p))


print("== 4. 数据层保留分队归属（磁盘归母队不等于把分队抹掉） ==")
mv_keep = MusicVideo(name="keep", video_type="PerformanceVideo", video_types=["PerformanceVideo"])
mv_keep.groups.append(g_sub)
db.add(mv_keep)
db.commit()
db.refresh(mv_keep)
check("mv.groups 仍挂小分队（不改写数据层）", [g.id for g in mv_keep.groups] == [g_sub.id])
check(
    "expand_group_ids(母队) 聚合旗下小分队",
    set(expand_group_ids(db, g_root.id)) == {g_root.id, g_sub.id, g_sub2.id, g_deep.id},
    sorted(expand_group_ids(db, g_root.id)),
)
check(
    "expand_group_ids(小分队) 含自身与下级",
    set(expand_group_ids(db, g_sub.id)) == {g_sub.id, g_deep.id},
    sorted(expand_group_ids(db, g_sub.id)),
)


print("== 5. 无 Session 时不上溯（游离对象 / 既有单测语义不变） ==")
mv_free = MusicVideo(name="free", video_type="PerformanceVideo", video_types=["PerformanceVideo"])
mv_free.groups.append(Group(name="EVOLution"))
p = auto_organize_rel(mv_free)
check("游离对象用挂上的那个名字", posix(p) == "EVOLution/Live", posix(p))

mv_free_short = MusicVideo(name="free2", video_type="ShortVideo", video_types=["ShortVideo"], is_short=True)
mv_free_short.groups.append(Group(name="EVOLution"))
p = auto_organize_rel(mv_free_short)
check("游离对象 短视频同理", posix(p) == "短视频/EVOLution", posix(p))


print("== 6. 更改存储路径 / 存量重整 同口径 ==")
mv_relo = MusicVideo(
    name="relo",
    video_type="PerformanceVideo",
    video_types=["PerformanceVideo"],
    ingestion_status="library",
    file_path="MV/EVOLution/relo.mp4",
)
mv_relo.groups.append(g_sub)
mv_relo.songs.append(Song(name="X"))
db.add(mv_relo)
db.commit()
db.refresh(mv_relo)
sugg = suggest_relocate_path(mv_relo, db=db)
check("旧分队路径 → 建议改成母队路径", posix(sugg) == "TripleS/X/Live/relo.mp4", posix(sugg))

items = plan_reorganize(db)
row = next((i for i in items if i["id"] == mv_relo.id), None)
check("存量重整把它列为待移动", row is not None, row)
check(
    "存量重整的建议路径与上溯一致",
    row is not None and row["suggested_path"] == "TripleS/X/Live/relo.mp4",
    row["suggested_path"] if row else "None",
)


print("== 7. B站重命名：文件名主体段同口径 ==")
mv_bili = MusicVideo(
    name="bili",
    video_type="PerformanceVideo",
    video_types=["PerformanceVideo"],
    performance_date=date(2024, 5, 1),
)
mv_bili.groups.append(g_sub)
mv_bili.songs.append(Song(name="Girls Never Die"))
mv_bili._bili_info = {"extractor": "bilibili", "id": "BV1xx411c7mD"}
db.add(mv_bili)
db.commit()
bili_name = _bili_rename(mv_bili, Path("dummy.mp4"), None, db)
check(
    "B站文件名主体取母队",
    bili_name == "240501-TripleS-Girls Never Die(BV1xx411c7mD).mp4",
    bili_name,
)
check(
    "母队主体也走中文名优先（与目录段一致）",
    bili_name is not None and bili_name.startswith("240501-TripleS-"),
    bili_name,
)

print(f"\n===== 结果: PASS={PASS} FAIL={FAIL} =====")
db.close()
sys.exit(1 if FAIL else 0)
