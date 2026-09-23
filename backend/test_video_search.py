"""视频列表搜索：标题 + 歌曲 / 艺人 / 组合名 / 来源 ID。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="video_search_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from sqlalchemy import select  # noqa: E402

from app.api.music_videos import _filter_by_query_and_types  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.models.song import Song  # noqa: E402

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
song = Song(name="Lemon Tang", chinese_name="柠檬糖")
artist = Artist(name="Jiwoo", chinese_name="智禹", stage_name="JIWOO")
group = Group(name="Hearts2Hearts", chinese_name="哈茨图哈茨")
hit = MusicVideo(
    name="fancam clip",
    ingestion_status="library",
    video_type="PersonalFancam",
    source_platform="bilibili",
    source_id="BV1987654321",
)
miss = MusicVideo(
    name="unrelated mv",
    ingestion_status="library",
    video_type="OfficialMV",
    source_platform="bilibili",
    source_id="BV1unrelated00",
)
hit.songs.append(song)
hit.artists.append(artist)
hit.groups.append(group)
db.add_all([song, artist, group, hit, miss])
db.commit()
hit_id = hit.id
db.close()


def search_ids(q: str) -> list[int]:
    s = SessionLocal()
    try:
        stmt = select(MusicVideo.id).where(MusicVideo.deleted_at.is_(None))
        stmt = _filter_by_query_and_types(stmt, q, None, None)
        return list(s.scalars(stmt))
    finally:
        s.close()


check("歌名命中", search_ids("Lemon") == [hit_id], str(search_ids("Lemon")))
check("歌曲中文名命中", search_ids("柠檬") == [hit_id], str(search_ids("柠檬")))
check("艺人名命中", search_ids("Jiwoo") == [hit_id], str(search_ids("Jiwoo")))
check("艺人中文名命中", search_ids("智禹") == [hit_id], str(search_ids("智禹")))
check("组合名命中", search_ids("Hearts2Hearts") == [hit_id], str(search_ids("Hearts2Hearts")))
check("组合中文名命中", search_ids("哈茨") == [hit_id], str(search_ids("哈茨")))
check("视频标题仍可用", search_ids("fancam") == [hit_id], str(search_ids("fancam")))
check("来源 ID 全量命中", search_ids("BV1987654321") == [hit_id], str(search_ids("BV1987654321")))
check("来源 ID 片段命中", search_ids("987654321") == [hit_id], str(search_ids("987654321")))
check("无关来源 ID 不命中", hit_id not in search_ids("BV1unrelated00"))
check("无关视频不命中", search_ids("Lemon") != [] and miss.id not in search_ids("Lemon"))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
