"""翻唱类型：入库目录、原唱展示名推导、AI 同义词。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="cover_stage_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.music_video import MusicVideo, MusicVideoTrack  # noqa: E402
from app.models.song import Song, SongArtistRelation  # noqa: E402
from app.services.ai_service import VIDEO_TYPES, _canonical_video_type  # noqa: E402
from app.services.library_service import auto_organize_rel  # noqa: E402
from app.services.video_meta import cover_from_names  # noqa: E402

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


# ----- AI 枚举 -----
check("CoverStage 在合法类型中", "CoverStage" in VIDEO_TYPES)
check("Audio 已从合法类型移除", "Audio" not in VIDEO_TYPES)
check("同义词 翻唱 → CoverStage", _canonical_video_type("翻唱") == "CoverStage")
check("同义词 cover → CoverStage", _canonical_video_type("cover") == "CoverStage")
check("同义词 Audio → Other", _canonical_video_type("Audio") == "Other")
check("同义词 音源 → Other", _canonical_video_type("音源") == "Other")


# ----- 入库目录 -----
def organize(video_types, *, group=None, artist=None, song=None, is_short=False):
    mv = MusicVideo(name="clip", video_types=list(video_types), is_short=is_short)
    if group:
        mv.groups.append(Group(name=group))
    if artist:
        mv.artists.append(Artist(name=artist, stage_name=artist))
    if song:
        mv.songs.append(Song(name=song))
    return auto_organize_rel(mv)


p = organize(["CoverStage"], group="IVE", song="Attention")
check(
    "翻唱目录 无艺人回退组合",
    p is not None and p.as_posix() == "翻唱/IVE/Attention",
    p.as_posix() if p else "None",
)

p = organize(["CoverStage"], group="IVE", artist="Wonyoung", song="Attention")
check(
    "翻唱目录 艺人优先",
    p is not None and p.as_posix() == "翻唱/Wonyoung/Attention",
    p.as_posix() if p else "None",
)

p = organize(["CoverStage"], artist="IU", song="Through the Night")
check(
    "无组合时用艺人",
    p is not None and p.as_posix() == "翻唱/IU/Through the Night",
    p.as_posix() if p else "None",
)

p = organize(["CoverStage", "OfficialMV"], group="IVE", song="Attention")
check(
    "翻唱+MV 仍走翻唱目录",
    p is not None and p.as_posix() == "翻唱/IVE/Attention",
    p.as_posix() if p else "None",
)

p = organize(["CoverStage", "LivePerformance"], group="IVE", song="Attention", is_short=True)
check(
    "短视频优先于翻唱（无艺人用组合）",
    p is not None and p.as_posix() == "短视频/IVE",
    p.as_posix() if p else "None",
)

p = organize(["CoverStage"], group="IVE")
check(
    "翻唱缺歌曲跳层",
    p is not None and p.as_posix() == "翻唱/IVE",
    p.as_posix() if p else "None",
)

p = organize(["OfficialMV"], group="IVE", song="Attention")
check(
    "非翻唱 MV 仍走 MV 目录",
    p is not None and p.as_posix() == "IVE/Attention/MV",
    p.as_posix() if p else "None",
)


# ----- 原唱展示名（不建视频级关联） -----
orig = Group(name="NewJeans", chinese_name="NewJeans")
song = Song(name="Attention")
song.artist_relations.append(
    SongArtistRelation(role="PrimaryArtist", group=orig, order=0)
)
track = MusicVideoTrack(song=song, position=0)

cover_mv = MusicVideo(name="ive-cover", video_types=["CoverStage"])
cover_mv.groups.append(Group(name="IVE"))
cover_mv.video_tracks.append(track)
names = cover_from_names(cover_mv)
check("翻唱自取原曲主唱", names == ["NewJeans"], str(names))

plain = MusicVideo(name="official", video_types=["OfficialMV"])
plain.video_tracks.append(track)
check("非翻唱不返回 cover_from", cover_from_names(plain) == [])

empty = MusicVideo(name="cover-no-artist", video_types=["CoverStage"])
empty.video_tracks.append(MusicVideoTrack(song=Song(name="Unknown"), position=0))
check("原曲无主唱时 cover_from 为空", cover_from_names(empty) == [])

# 发行主体兜底（需要 session.get）
db = SessionLocal()
try:
    nj = Group(name="NewJeans")
    db.add(nj)
    db.flush()
    rel_song = Song(name="Hype Boy", release_artist_type="group", release_artist_id=nj.id)
    rel_mv = MusicVideo(name="cover-rel", video_types=["CoverStage"])
    rel_mv.video_tracks.append(MusicVideoTrack(song=rel_song, position=0))
    db.add(rel_mv)
    db.flush()
    fallback = cover_from_names(rel_mv)
    check("发行主体兜底", fallback == ["NewJeans"], str(fallback))
    db.rollback()
finally:
    db.close()


print(f"\n===== 结果: PASS={PASS} FAIL={FAIL} =====")
sys.exit(0 if FAIL == 0 else 1)
