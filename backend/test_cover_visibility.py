"""翻唱视频「只跟随表演主体」（v3.2.47）验收测试。

用户要求：A 翻唱 B 的歌，翻唱视频只体现在 A 的主页，不体现在 B 的主页。

三条查询口径（GET /music-videos 的 list_videos）：
  ① song_id     （原唱歌曲详情页）    → 一律排除翻唱
  ② artist_id   （艺人详情页）        → 仅 subject 命中（或 subject 为空退回关联艺人）时显示
  ③ group_id    （组合详情页，含小分队家族）→ 仅 subject 艺人（或 subject 为空退回关联艺人）
     的成员关系命中家族时显示

直调路由函数有 Query 默认值陷阱（缺省参数是 Query 对象），必须全部显式传参；
分页用 schemas 的 PageRequest 形参（page/page_size 走 PaginationDep，直接给 int 即可）。

只跑本文件：
    python test_cover_visibility.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="cover_visibility_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from datetime import datetime  # noqa: E402

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.api.music_videos import list_videos  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.membership import GroupMembership  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.models.song import Song  # noqa: E402
from app.core.deps import PaginationDep  # noqa: E402

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


def mk_group(name):
    g = Group(name=name)
    db.add(g)
    db.flush()
    return g


def mk_artist(name):
    a = Artist(name=name)
    db.add(a)
    db.flush()
    return a


def mk_membership(artist, group):
    m = GroupMembership(group_id=group.id, artist_id=artist.id, status="Active")
    db.add(m)
    db.flush()
    return m


def mk_video(name, video_type="PerformanceVideo", song=None, artists=(),
             groups=(), subject=None, video_types=None):
    mv = MusicVideo(name=name, video_type=video_type,
                    video_types=video_types or [video_type],
                    ingestion_status="library")
    if song is not None:
        mv.song_id = song.id
        mv.songs.append(song)
    for a in artists:
        mv.artists.append(a)
    for g in groups:
        mv.groups.append(g)
    if subject is not None:
        mv.subject_artist_id = subject.id
    db.add(mv)
    db.flush()
    return mv


def query(**kw):
    """直调 list_videos：显式传全部 Query 参数，避免缺省 Query 对象真值陷阱。"""
    class P:
        page = 1
        page_size = 200
        offset = 0
        limit = 200

    kw.setdefault("q", None)
    kw.setdefault("video_type", None)
    kw.setdefault("video_types", None)
    kw.setdefault("song_id", None)
    kw.setdefault("subject_artist_id", None)
    kw.setdefault("artist_id", None)
    kw.setdefault("group_id", None)
    kw.setdefault("ingestion_status", None)
    kw.setdefault("uploader", None)
    kw.setdefault("is_short", None)
    kw.setdefault("resolution", None)
    kw.setdefault("sort", None)
    kw["db"] = db
    kw["pagination"] = P()
    return list_videos(**kw)


# ===== 夹具：Red Velvet 的歌《Red Flavor》，Yuha（Hearts2Hearts 成员）翻唱 =====
g_rv = mk_group("Red Velvet")
g_h2h = mk_group("Hearts2Hearts")
a_yuha = mk_artist("Yuha")
mk_membership(a_yuha, g_h2h)

song_rf = Song(name="Red Flavor", release_artist_type="group",
               release_artist_id=g_rv.id)
db.add(song_rf)
db.flush()

# 原唱 MV（Red Velvet）
mv_orig = mk_video("Red Velvet - Red Flavor MV", video_type="OfficialMV",
                   song=song_rf, groups=[g_rv])
# 翻唱（Yuha，subject 明确）
mv_cover = mk_video("Yuha - Red Flavor Cover", video_type="CoverStage",
                    song=song_rf, artists=[a_yuha], groups=[g_h2h],
                    subject=a_yuha)
# 翻唱（subject 未定的历史数据：只有关联艺人/组合）
a_arts = mk_artist("HeeJin")
g_arts = mk_group("ARTMS")
mk_membership(a_arts, g_arts)
song_bn = Song(name="Be Natural", release_artist_type="group",
               release_artist_id=g_rv.id)
db.add(song_bn)
db.flush()
mv_cover_nosub = mk_video("ARTMS - Be Natural Cover", video_type="CoverStage",
                          song=song_bn, artists=[a_arts], groups=[g_arts])
# 异常数据：原唱组合被误挂进翻唱关联（回归防线）
mv_cover_leak = mk_video("Yuha - Bad Case Cover", video_type="CoverStage",
                         song=song_rf, artists=[a_yuha],
                         groups=[g_h2h, g_rv], subject=a_yuha)
# video_types 数组形式的翻唱（主类型是 Fancam）
mv_cover_arr = mk_video("Yuha - Red Flavor Cover Fancam",
                        video_type="Fancam", song=song_rf,
                        artists=[a_yuha], groups=[g_h2h], subject=a_yuha,
                        video_types=["Fancam", "CoverStage"])
# 合法形态：组合翻唱、subject 未定、**只有组合关联没有艺人关联**（纯组合 Cover）
# subject 不可判定 → 放行保持老行为，组合页必须能看到
mv_cover_grouponly = mk_video("ARTMS - Group Cover", video_type="CoverStage",
                              song=song_bn, groups=[g_arts])
db.commit()


def ids(resp):
    return sorted(mv.id for mv in resp.items)


print("== 1. song_id（原唱歌曲详情页）：翻唱一律不出现 ==")
r = query(song_id=song_rf.id)
check("歌曲页只留原唱 MV", ids(r) == [mv_orig.id], ids(r))
r = query(song_id=song_bn.id)
check("Be Natural 歌曲页不含 subject 未定的翻唱", mv_cover_nosub.id not in ids(r), ids(r))

print("== 2. artist_id（艺人详情页）：翻唱只跟随表演主体 ==")
r = query(artist_id=a_yuha.id)
check("翻唱者 Yuha 主页能看到自己的翻唱", mv_cover.id in ids(r), ids(r))
check("video_types 数组形式的翻唱也算翻唱", mv_cover_arr.id in ids(r), ids(r))
r = query(artist_id=a_arts.id)
check("subject 未定时退回关联艺人（HeeJin 主页可见）", mv_cover_nosub.id in ids(r), ids(r))

print("== 3. group_id（组合详情页，含家族） ==")
r = query(group_id=g_h2h.id)
check("表演方组合 H2H 主页显示翻唱",
      {mv_cover.id, mv_cover_leak.id, mv_cover_arr.id}.issubset(set(ids(r))), ids(r))
r = query(group_id=g_rv.id)
check("原唱组合 RV 主页不出现任何翻唱（含误挂数据）",
      not ({mv_cover.id, mv_cover_leak.id, mv_cover_arr.id,
            mv_cover_nosub.id} & set(ids(r))), ids(r))
check("原唱组合 RV 主页保留原唱 MV", mv_orig.id in ids(r), ids(r))
r = query(group_id=g_arts.id)
check("subject 未定退回：ARTMS 主页可见自己的翻唱", mv_cover_nosub.id in ids(r), ids(r))
check("subject 未定且仅组合关联：组合页同样可见（不误伤纯组合 Cover）",
      mv_cover_grouponly.id in ids(r), ids(r))

print("== 4. 不带实体过滤的普通列表不受影响 ==")
r = query()
check("全量列表包含所有视频", len(ids(r)) == 6, ids(r))

db.close()
print(f"\n==== {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
