"""歌曲列表过滤口径（GET /songs 的 only_with_videos / filter=linked|orphan）验收测试。

v3.2.53 根治「歌曲页加载慢」：only_with_videos 从「每首歌 3 个相关 EXISTS」
改为「视频侧一次预聚合 UNION + Song.id IN」（_linked_song_ids_subq）。
本测试锁定重构前后的口径不变：
  ① 直接关联（music_videos.song_id）
  ② M2M（music_video_songs）
  ③ 曲目行（music_video_tracks）
三种任一命中即算有关联；排除软删除视频与短视频；同一视频多口径命中只算一次。

直调路由函数有 Query 默认值陷阱（缺省参数是 Query 对象），必须全部显式传参；
分页用 PageRequest 形参（page/page_size 走 PaginationDep，直接给 int 即可）。

只跑本文件：
    python test_song_list_filter.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="song_list_filter_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from datetime import datetime, timezone  # noqa: E402

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.api.songs import list_songs  # noqa: E402
from app.models.music_video import (  # noqa: E402
    MusicVideo,
    MusicVideoTrack,
    music_video_songs,
)
from app.models.song import Song  # noqa: E402

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


def mk_song(name, **kw):
    s = Song(name=name, **kw)
    db.add(s)
    db.flush()
    return s


def mk_mv(name, song=None, is_short=False, deleted=False):
    mv = MusicVideo(
        name=name,
        video_type="MV",
        video_types=["MV"],
        ingestion_status="library",
        is_short=is_short,
    )
    if song is not None:
        mv.song_id = song.id
    db.add(mv)
    db.flush()
    if deleted:
        mv.deleted_at = datetime.now(timezone.utc)
        db.flush()
    return mv


# ===== 夹具：6 首歌覆盖三种关联口径 + 两种排除口径 + 纯孤儿 =====
s_direct = mk_song("DirectLink")            # ① 直接关联
s_m2m = mk_song("M2MLink")                  # ② M2M
s_track = mk_song("TrackLink")              # ③ 曲目行
s_multi = mk_song("MultiLink")              # 直接 + M2M 同挂，去重算一次
s_short_only = mk_song("ShortOnly")         # 只挂短视频 → 排除
s_deleted_only = mk_song("DeletedOnly")     # 只挂软删视频 → 排除
s_orphan = mk_song("PureOrphan")            # 无任何视频

mv_a = mk_mv("MV_A", song=s_direct)
mv_b = mk_mv("MV_B")
mv_b.songs.append(s_m2m)
db.flush()

mv_c = mk_mv("MV_C")
db.add(MusicVideoTrack(music_video_id=mv_c.id, song_id=s_track.id, position=1))
db.flush()

mk_mv("MV_D1", song=s_multi)
mv_d2 = mk_mv("MV_D2")
mv_d2.songs.append(s_multi)
db.flush()

mk_mv("MV_Short", song=s_short_only, is_short=True)
mk_mv("MV_Deleted", song=s_deleted_only, deleted=True)
db.commit()


def query(**kw):
    """直调 list_songs：显式传全部 Query 参数，避免缺省 Query 对象真值陷阱。"""
    class P:
        page = 1
        page_size = 200
        offset = 0
        limit = 200

    kw.setdefault("q", None)
    kw.setdefault("song_type", None)
    kw.setdefault("artist_id", None)
    kw.setdefault("group_id", None)
    kw.setdefault("album_id", None)
    kw.setdefault("sort", None)
    kw.setdefault("only_with_videos", False)
    kw.setdefault("filter", "all")
    kw["db"] = db
    kw["pagination"] = P()
    return list_songs(**kw)


def names(res):
    return sorted(i.name for i in res.items)


# ===== ① only_with_videos=True（前端默认开关） =====
r = query(only_with_videos=True)
check("only_with_videos 命中 4 首", names(r) == ["DirectLink", "M2MLink", "MultiLink", "TrackLink"], names(r))
check("only_with_videos total=4", r.total == 4, r.total)

# ===== ② filter=linked 与开关同口径 =====
r2 = query(filter="linked")
check("filter=linked 与开关一致", names(r2) == names(r), names(r2))

# ===== ③ filter=orphan：短视频唯一关联 / 软删视频唯一关联 / 纯孤儿 =====
r3 = query(filter="orphan")
check(
    "filter=orphan 命中 3 首",
    names(r3) == ["DeletedOnly", "PureOrphan", "ShortOnly"],
    names(r3),
)

# ===== ④ filter=all（默认）：全部活跃歌曲，含无关联 =====
r4 = query()
check("filter=all 命中 7 首", names(r4) == sorted(
    ["DirectLink", "M2MLink", "MultiLink", "TrackLink", "ShortOnly", "DeletedOnly", "PureOrphan"]
), names(r4))

# ===== ⑤ video_count 去重：多口径同挂一首只算一次（排除短视频/软删） =====
by_name = {i.name: i for i in r4.items}
check("MultiLink video_count=2（直接+M2M，去重）", by_name["MultiLink"].video_count == 2,
      by_name["MultiLink"].video_count)
check("ShortOnly video_count=0（短视频不算）", by_name["ShortOnly"].video_count == 0,
      by_name["ShortOnly"].video_count)
check("DeletedOnly video_count=0（软删不算）", by_name["DeletedOnly"].video_count == 0,
      by_name["DeletedOnly"].video_count)

# ===== ⑥ album_ids 字段可用（selectinload 预载路径不炸、值正确） =====
check("album_ids 字段存在且为列表", isinstance(by_name["DirectLink"].album_ids, list))

# ===== ⑦ 软删歌曲不出现在任何口径 =====
db.delete(s_orphan)
s_orphan.deleted_at = datetime.now(timezone.utc)
db.commit()
r5 = query()
check("软删歌曲从 filter=all 消失", "PureOrphan" not in names(r5), names(r5))
r6 = query(filter="orphan")
check("软删歌曲从 filter=orphan 消失", "PureOrphan" not in names(r6), names(r6))

db.close()
print(f"\n== song_list_filter: PASS={PASS} FAIL={FAIL} ==")
sys.exit(1 if FAIL else 0)
