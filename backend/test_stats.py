"""馆藏统计：库内排行、近 30 天入库、短视频排除、直拍对象计入艺人。"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="stats_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.incoming_file import IncomingFile  # noqa: E402
from app.models.music_video import MusicVideo, MusicVideoTrack  # noqa: E402
from app.models.song import Song  # noqa: E402
from app.models.watch import WatchPlay  # noqa: E402
from app.services.stats_service import library_stats, resolution_tier  # noqa: E402
from app.services.watch_service import record_watch  # noqa: E402

settings.ensure_directories()
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
g = Group(name="IZNA")
a1 = Artist(name="JEONG SAEBI", stage_name="SAEBI")
a2 = Artist(name="Kotone")
s1 = Song(name="TIME")
s2 = Song(name="B-side")
db.add_all([g, a1, a2, s1, s2])
db.flush()

old = datetime.utcnow() - timedelta(days=40)
now = datetime.utcnow()

def add_mv(**kw):
    mv = MusicVideo(
        name=kw.get("name", "v"),
        ingestion_status=kw.get("ingestion_status", "library"),
        duration=kw.get("duration", 100),
        is_short=kw.get("is_short", False),
        video_type=kw.get("video_type", "OfficialMV"),
        subject_artist_id=kw.get("subject_artist_id"),
        width=kw.get("width"),
        height=kw.get("height"),
    )
    db.add(mv)
    db.flush()
    if kw.get("created_at"):
        mv.created_at = kw["created_at"]
    if kw.get("groups"):
        mv.groups = kw["groups"]
    if kw.get("artists"):
        mv.artists = kw["artists"]
    if kw.get("song"):
        db.add(MusicVideoTrack(music_video_id=mv.id, song_id=kw["song"].id, position=0))
    return mv

# 旧正式 MV：组合 + 艺人 + 歌曲
add_mv(
    name="old-mv",
    groups=[g],
    artists=[a1],
    song=s1,
    duration=200,
    created_at=old,
    width=1920,
    height=1080,
)
# 近 30 天直拍：只挂组合，subject 是 a1（竖屏 4K 源，短边 2160）
add_mv(
    name="new-fancam",
    groups=[g],
    subject_artist_id=a1.id,
    song=s1,
    duration=180,
    video_type="PersonalFancam",
    created_at=now,
    width=2160,
    height=3840,
)
# 近 30 天短视频：组合 + a2
add_mv(
    name="short",
    groups=[g],
    artists=[a2],
    song=s2,
    duration=20,
    is_short=True,
    created_at=now,
    width=720,
    height=1280,
)
# 待整理不进榜（带 4K 宽高，用于验证不串进分辨率构成）
add_mv(
    name="incoming",
    ingestion_status="incoming",
    groups=[g],
    artists=[a1],
    song=s1,
    width=3840,
    height=2160,
)
db.add(
    IncomingFile(
        file_path=str(_tmp / "incoming" / "sample.mp4"),
        file_name="sample.mp4",
        file_size=123,
        mtime=now.timestamp(),
    )
)
db.commit()
db.close()

db = SessionLocal()
all_no_short = library_stats(db, range_key="all", include_shorts=False, limit=10)
ov = all_no_short["overview"]
check("已整理 3 条（含短视频）", ov["library_count"] == 3, str(ov["library_count"]))
check("待整理 1", ov["incoming_count"] == 1)
check("短视频 1", ov["shorts_count"] == 1)
check("近 30 天入库 2", ov["last_30d_count"] == 2, str(ov["last_30d_count"]))
check("总时长含短视频", ov["total_duration_seconds"] == 400, str(ov["total_duration_seconds"]))
check("观看榜已接入", all_no_short["watch"]["available"] is True)
check("尚无观看时次数为 0", all_no_short["watch"]["play_count"] == 0)

# ===== 分辨率构成 =====
rb = ov.get("resolution_breakdown")
check("分辨率构成为字典", isinstance(rb, dict), str(rb))
check(
    "竖屏 4K 按短边落 4k（非 8k）",
    rb.get("4k") == 1,
    str(rb),
)
check("1080p 计数", rb.get("1080p") == 1, str(rb))
check("720p 计数", rb.get("720p") == 1, str(rb))
check("空档不返回", "8k" not in rb and "2k" not in rb and "sd" not in rb, str(rb))
check("档位按 8K→低清 排序", list(rb) == ["4k", "1080p", "720p"], str(list(rb)))
check(
    "待整理视频不计入分辨率构成",
    sum(rb.values()) == 3,
    str(rb),
)

# 档位归档单元测试（含竖屏、缺字段、越界值）
check("8K 横屏", resolution_tier(7680, 4320) == "8k")
check("8K 竖屏取短边", resolution_tier(4320, 7680) == "8k")
check("4K 竖屏取短边", resolution_tier(2160, 3840) == "4k")
check("2K 不被误判为 1080P", resolution_tier(2560, 1440) == "2k")
check("1080P 横屏", resolution_tier(1920, 1080) == "1080p")
check("720P", resolution_tier(1280, 720) == "720p")
check("480P", resolution_tier(854, 480) == "480p")
check("360P 独立成档（不并进 480P）", resolution_tier(640, 360) == "360p")
check("240P 独立成档", resolution_tier(426, 240) == "240p")
check("240P 竖屏取短边", resolution_tier(240, 426) == "240p")
check("低于 240P 才归 sd 兜底", resolution_tier(256, 144) == "sd")
check("缺宽高归 unknown", resolution_tier(None, None) == "unknown")
check("单边缺失归 unknown（不猜档位）", resolution_tier(1920, None) == "unknown")
check("0 值归 unknown", resolution_tier(0, 0) == "unknown")
check("非法值归 unknown", resolution_tier("abc", None) == "unknown")

# 库里真有「未探测」的视频时，unknown 排在最后（不被当成高清档）
mv_probe = db.scalar(select(MusicVideo).where(MusicVideo.name == "short"))
mv_probe.width = None
mv_probe.height = None
db.commit()
rb2 = library_stats(db, range_key="all", include_shorts=False)["overview"][
    "resolution_breakdown"
]
check("unknown 排最后", list(rb2) == ["4k", "1080p", "unknown"], str(rb2))
mv_probe.width = 720
mv_probe.height = 1280
db.commit()

groups = {r["name"]: r for r in all_no_short["groups"]}
check("组合榜排除短视频为 2", groups["IZNA"]["video_count"] == 2, str(groups.get("IZNA")))
check("组合时长 380", groups["IZNA"]["duration_seconds"] == 380)

artists = {r["name"]: r for r in all_no_short["artists"]}
check("直拍对象计入艺人", artists["JEONG SAEBI"]["video_count"] == 2, str(artists.get("JEONG SAEBI")))
check("短视频艺人未进默认榜", "Kotone" not in artists)

songs = {r["name"]: r for r in all_no_short["songs"]}
check("歌曲 TIME=2", songs["TIME"]["video_count"] == 2, str(songs))
check("短视频歌曲未进默认榜", "B-side" not in songs)

d30 = library_stats(db, range_key="30d", include_shorts=False, limit=10)
g30 = {r["name"]: r for r in d30["groups"]}
check("30 天组合排除短视频为 1", g30["IZNA"]["video_count"] == 1, str(g30.get("IZNA")))
a30 = {r["name"]: r for r in d30["artists"]}
check("30 天艺人只有直拍对象", list(a30) == ["JEONG SAEBI"], str(list(a30)))

with_short = library_stats(db, range_key="all", include_shorts=True, limit=10)
gs = {r["name"]: r for r in with_short["groups"]}
check("含短视频组合为 3", gs["IZNA"]["video_count"] == 3)
as_ = {r["name"]: r for r in with_short["artists"]}
check("含短视频 Kotone 进榜", as_["Kotone"]["video_count"] == 1)

mv_new = db.scalar(select(MusicVideo).where(MusicVideo.name == "new-fancam"))
mv_old = db.scalar(select(MusicVideo).where(MusicVideo.name == "old-mv"))
mv_short = db.scalar(select(MusicVideo).where(MusicVideo.name == "short"))
check("测观看用视频存在", bool(mv_new and mv_old and mv_short))

r0 = record_watch(
    db, music_video_id=mv_new.id, position=0, duration=180, playing=True
)
play = db.get(WatchPlay, r0["play_id"])
play.last_seen_at = datetime.utcnow() - timedelta(seconds=12)
play.last_position = 0
db.commit()
r1 = record_watch(
    db, music_video_id=mv_new.id, position=12, duration=180, playing=True
)
check("12 秒未达门槛不进榜", library_stats(db)["watch"]["play_count"] == 0, str(r1))

play = db.get(WatchPlay, r1["play_id"])
play.last_seen_at = datetime.utcnow() - timedelta(seconds=30)
play.last_position = 12
db.commit()
r2 = record_watch(
    db, music_video_id=mv_new.id, position=50, duration=180, playing=True
)
st = library_stats(db, range_key="all", include_shorts=False)
check("观看可用", st["watch"]["available"] is True)
check("达到门槛计 1 次", st["watch"]["play_count"] == 1, str(st["watch"]))
check("累计秒数大约 42", 30 <= st["watch"]["seconds_watched"] <= 50, str(st["watch"]["seconds_watched"]))
check("组合观看进榜", st["watch"]["groups"] and st["watch"]["groups"][0]["name"] == "IZNA")
check("艺人观看含直拍对象", any(x["name"] == "JEONG SAEBI" for x in st["watch"]["artists"]))
check("歌曲观看含 TIME", any(x["name"] == "TIME" for x in st["watch"]["songs"]))
check("视频观看含 new-fancam", any(x["name"] == "new-fancam" for x in st["watch"]["videos"]))

play = db.get(WatchPlay, r2["play_id"])
before = play.seconds_watched
play.last_seen_at = datetime.utcnow() - timedelta(seconds=2)
play.last_position = 50
db.commit()
record_watch(db, music_video_id=mv_new.id, position=170, duration=180, playing=True)
play = db.get(WatchPlay, r2["play_id"])
check("seek 前跳不把秒数撑爆", play.seconds_watched - before <= 5, str(play.seconds_watched - before))

record_watch(
    db, music_video_id=mv_new.id, position=175, duration=180, playing=False, ended=True
)
play = db.get(WatchPlay, r2["play_id"])
check("看完标记 completed", play.completed is True)
st2 = library_stats(db)
check("看完计数", st2["watch"]["completed_count"] == 1, str(st2["watch"]["completed_count"]))

# 旧片观看落在 40 天前，30d 观看榜不应计入
old_play = WatchPlay(
    music_video_id=mv_old.id,
    started_at=old,
    last_seen_at=old,
    last_position=80,
    max_position=80,
    seconds_watched=80,
    source_duration=200,
    completed=True,
)
db.add(old_play)
db.commit()
st_all = library_stats(db, range_key="all", include_shorts=False)
st_30 = library_stats(db, range_key="30d", include_shorts=False)
check("全部观看含旧片", st_all["watch"]["play_count"] == 2, str(st_all["watch"]["play_count"]))
check("30 天观看不含旧片", st_30["watch"]["play_count"] == 1, str(st_30["watch"]["play_count"]))

record_watch(db, music_video_id=mv_short.id, position=0, duration=20, playing=True)
sp = db.scalar(select(WatchPlay).where(WatchPlay.music_video_id == mv_short.id))
sp.last_seen_at = datetime.utcnow() - timedelta(seconds=20)
sp.last_position = 0
sp.seconds_watched = 20
sp.completed = True
db.commit()
no_short = library_stats(db, include_shorts=False)
yes_short = library_stats(db, include_shorts=True)
check("默认观看榜不含短视频", no_short["watch"]["play_count"] == 2, str(no_short["watch"]["play_count"]))
check("含短视频观看榜 +1", yes_short["watch"]["play_count"] == 3, str(yes_short["watch"]["play_count"]))

db.close()

print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
