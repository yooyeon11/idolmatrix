"""小分队视频「母队口径含小分队」（v3.2.43）验收测试。

用户要求：显示时，母队的全部视频也要显示小分队的视频。

三条展示口径必须一致 —— 否则会「列表点进去 5 条、列表上写 4 条」：
  ① 视频列表筛选  GET /music-videos?group_id=   → group_tree.expand_group_ids
  ② 资料库组合列表 / 组合工作台「影像」数        → db_views._group_work_counts
  ③ 统计页组合排行                              → stats_service.rank_groups
三条都走 group_tree.family_group_ids。

有意**不**归并：专辑 / 歌曲仍是数据层的发行主体署名（「数据层留分队」），
同一条视频若同时挂母队与小分队只能算一条。

只跑本文件：
    python test_subunit_display.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="subunit_display_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from datetime import datetime  # noqa: E402

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.models.song import Song  # noqa: E402
from app.services.db_views import _group_work_counts  # noqa: E402
from app.services.group_tree import (  # noqa: E402
    expand_group_ids,
    family_group_ids,
)
from app.services.stats_service import rank_groups  # noqa: E402

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


def mk_group(name, parent=None, deleted=False):
    g = Group(name=name)
    if parent is not None:
        g.parent_group_id = parent.id
    if deleted:
        g.deleted_at = datetime(2024, 1, 1)
    db.add(g)
    db.flush()
    return g


def mk_video(name, groups, duration=100, deleted=False, is_short=False,
             status="library"):
    mv = MusicVideo(name=name, duration=duration, video_type="PerformanceVideo",
                    ingestion_status=status, is_short=is_short)
    if deleted:
        mv.deleted_at = datetime(2024, 1, 1)
    for g in groups:
        mv.groups.append(g)
    db.add(mv)
    db.flush()
    return mv


# ===== 夹具：母队 / 小分队 / 二级小分队 / 无关组 / 软删分队 =====
g_root = mk_group("tripleS")
g_sub = mk_group("EVOLution", parent=g_root)
g_deep = mk_group("DeepSub", parent=g_sub)          # 二级：分队的再分队
g_other = mk_group("aespa")                         # 无关组（无视频）
g_dead = mk_group("OldSub", parent=g_root, deleted=True)  # 软删分队
db.flush()

v_mother = mk_video("v1 只挂母队", [g_root], duration=100)
v_sub = mk_video("v2 只挂小分队", [g_sub], duration=200)
v_deep = mk_video("v3 只挂二级分队", [g_deep], duration=300)
v_both = mk_video("v4 母队+小分队同挂", [g_root, g_sub], duration=400)
v_deadmv = mk_video("v5 已软删视频", [g_sub], duration=9000, deleted=True)
v_short = mk_video("v6 短视频（挂小分队）", [g_sub], duration=50, is_short=True)
v_deadgroup = mk_video("v7 挂已软删分队", [g_dead], duration=8000)
v_incoming = mk_video("v8 未入库", [g_sub], duration=7000, status="incoming")

# 专辑/歌曲署名在小分队上 —— 用来确认「有意不归并」
db.add(Song(name="Girls Never Die", release_artist_type="group",
            release_artist_id=g_sub.id))
db.commit()


def ids_of(group_id):
    return sorted(expand_group_ids(db, group_id))


def counts():
    return _group_work_counts(db, [g_root.id, g_sub.id, g_deep.id, g_other.id])


print("== 1. family_group_ids：批量家族展开 ==")
fam = family_group_ids(db, [g_root.id, g_sub.id, g_deep.id, g_other.id])
check("母队 → 自身 + 两层小分队（软删的不算）",
      fam[g_root.id] == {g_root.id, g_sub.id, g_deep.id}, sorted(fam[g_root.id]))
check("小分队 → 自身 + 下级",
      fam[g_sub.id] == {g_sub.id, g_deep.id}, sorted(fam[g_sub.id]))
check("二级分队 → 只有自身", fam[g_deep.id] == {g_deep.id}, sorted(fam[g_deep.id]))
check("无子组合 → 只有自身", fam[g_other.id] == {g_other.id}, sorted(fam[g_other.id]))
check("空入参 → 空表", family_group_ids(db, []) == {})
check("入参去重且跳过 None", sorted(family_group_ids(db, [g_sub.id, g_sub.id, None])) == [g_sub.id])
check("软删组合不作为后代",
      g_dead.id not in fam[g_root.id], sorted(fam[g_root.id]))

print("\n== 2. expand_group_ids 回归（原单组合接口口径不变） ==")
check("母队展开含自身与全部活跃后代",
      set(ids_of(g_root.id)) == {g_root.id, g_sub.id, g_deep.id}, ids_of(g_root.id))
check("小分队展开含自身与下级",
      set(ids_of(g_sub.id)) == {g_sub.id, g_deep.id}, ids_of(g_sub.id))
check("孤儿组只返回自身", ids_of(g_other.id) == [g_other.id], ids_of(g_other.id))

print("\n== 3. 资料库组合列表 / 工作台：影像数走家族口径 ==")
# 影像数沿用原口径：短视频与未入库（incoming）记录照旧计入，只有软删不算。
#   母队家族 = v1 v2 v3 v4（同挂去重）+ v6（短视频）+ v8（未入库）= 6
#   小分队家族 = v2 v3 v4 + v6 + v8 = 5
c = counts()
check("母队影像 = 自己的 + 小分队的 + 二级的（同挂去重、软删不算）",
      c[g_root.id]["videos"] == 6, c[g_root.id])
check("小分队影像 = 自己的 + 二级的",
      c[g_sub.id]["videos"] == 5, c[g_sub.id])
check("二级分队影像 = 只有自己的",
      c[g_deep.id]["videos"] == 1, c[g_deep.id])
check("只挂软删分队的视频不计入母队（v7）",
      c[g_root.id]["videos"] == 6, c[g_root.id])
check("无关组影像 0", c[g_other.id]["videos"] == 0, c[g_other.id])
check("母队 > 小分队 > 二级（层层包含）",
      c[g_root.id]["videos"] > c[g_sub.id]["videos"] > c[g_deep.id]["videos"])
check("专辑 / 歌曲有意不归并（署名在小分队 → 母队仍为 0）",
      c[g_root.id]["albums"] == 0 and c[g_root.id]["songs"] == 0,
      c[g_root.id])
check("小分队自己的歌曲照常计入",
      c[g_sub.id]["songs"] == 1, c[g_sub.id])

print("\n== 4. 统计页组合排行：母队数字含小分队，小分队单列 ==")
rows = {r["name"]: r for r in rank_groups(db, range_key="all", include_shorts=False, limit=50)}
check("母队入榜且为家族合计", rows.get("tripleS", {}).get("video_count") == 4,
      rows.get("tripleS"))
check("母队时长也是家族合计（去重后）",
      rows.get("tripleS", {}).get("duration_seconds") == 1000,
      rows.get("tripleS"))
check("小分队仍单列一行（自身 + 下级）",
      rows.get("EVOLution", {}).get("video_count") == 3, rows.get("EVOLution"))
check("二级分队单列一行", rows.get("DeepSub", {}).get("video_count") == 1,
      rows.get("DeepSub"))
check("软删分队不入榜", "OldSub" not in rows, list(rows))
check("无视频的组合不入榜", "aespa" not in rows, list(rows))
check("排行按数量降序（母队在最前）",
      next(iter(rows)) == "tripleS", list(rows)[:3])

print("\n== 5. 统计页筛选开关仍然生效（短视频 / 时间窗） ==")
rows_short = {
    r["name"]: r
    for r in rank_groups(db, range_key="all", include_shorts=True, limit=50)
}
check("include_shorts=True → 母队 +1 条（小分队的短视频一并算进来）",
      rows_short["tripleS"]["video_count"] == 5, rows_short["tripleS"])
check("include_shorts=True → 小分队 +1 条",
      rows_short["EVOLution"]["video_count"] == 4, rows_short["EVOLution"])
check("include_shorts=True 时长同步增加",
      rows_short["tripleS"]["duration_seconds"] == 1050,
      rows_short["tripleS"])
rows_30d = {
    r["name"]: r
    for r in rank_groups(db, range_key="30d", include_shorts=False, limit=50)
}
check("30 天窗口口径可用（夹具全是刚建的 → 仍然入榜）",
      rows_30d.get("tripleS", {}).get("video_count") == 4, rows_30d.get("tripleS"))

print("\n== 6. 单组合场景（列表只问一个组） ==")
one = _group_work_counts(db, [g_root.id])
check("只问母队时数字一致", one[g_root.id]["videos"] == 6, one)
one_sub = _group_work_counts(db, [g_sub.id])
check("只问小分队时不含兄弟分队", one_sub[g_sub.id]["videos"] == 5, one_sub)

db.close()
print(f"\n==== {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
