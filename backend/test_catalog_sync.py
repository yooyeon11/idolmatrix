"""视频去掉专辑后，目录在无其它引用时删除。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="catalog_sync_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.album import Album, AlbumTrack  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.models.song import Song  # noqa: E402
from app.services.relation_service import apply_video_song_tracks  # noqa: E402
from app.services.video_meta import normalize_video_types  # noqa: E402

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


def load_mv(mv_id: int) -> MusicVideo:
    db = SessionLocal()
    mv = db.scalar(
        select(MusicVideo)
        .options(
            selectinload(MusicVideo.video_tracks).selectinload(
                __import__("app.models.music_video", fromlist=["MusicVideoTrack"]).MusicVideoTrack.albums
            )
        )
        .where(MusicVideo.id == mv_id)
    )
    return db, mv


db = SessionLocal()
song = Song(name="Track A")
album = Album(name="Album A")
album2 = Album(name="Album B")
mv = MusicVideo(name="pv", ingestion_status="library", video_type="OfficialMV")
db.add_all([song, album, album2, mv])
db.flush()
apply_video_song_tracks(
    db, mv, tracks=[{"song_id": song.id, "album_ids": [album.id, album2.id]}]
)
db.commit()
sid, aid, aid2, mid = song.id, album.id, album2.id, mv.id
db.close()

db = SessionLocal()
n = db.scalar(select(AlbumTrack).where(AlbumTrack.song_id == sid, AlbumTrack.album_id == aid))
check("写入后目录有专辑 A", n is not None)
db.close()

from app.models.music_video import MusicVideoTrack  # noqa: E402

db = SessionLocal()
mv = db.scalar(
    select(MusicVideo)
    .options(selectinload(MusicVideo.video_tracks).selectinload(MusicVideoTrack.albums))
    .where(MusicVideo.id == mid)
)
apply_video_song_tracks(db, mv, tracks=[{"song_id": sid, "album_ids": [aid2]}])
db.commit()
db.close()

db = SessionLocal()
a_row = db.scalar(select(AlbumTrack).where(AlbumTrack.song_id == sid, AlbumTrack.album_id == aid))
b_row = db.scalar(select(AlbumTrack).where(AlbumTrack.song_id == sid, AlbumTrack.album_id == aid2))
check("拿掉专辑 A 后目录删除 A", a_row is None)
check("仍选中的专辑 B 保留", b_row is not None)
db.close()

p, types = normalize_video_types(["PersonalFancam", "OfficialMV"], None)
check("类型归一主类型取第一项", p == "PersonalFancam" and types[0] == "PersonalFancam")
p, types = normalize_video_types(None, "OfficialMV")
check("只有 video_type 时填入数组", types == ["OfficialMV"] and p == "OfficialMV")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
