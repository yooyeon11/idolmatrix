"""照片缩略图写入 derived，不改动原文件。"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="photo_thumbs_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")
os.environ["PHOTO_BROWSE_ROOTS"] = str(_tmp)

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.core.ffmpeg import resolve_binaries  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.photo import Photo, PhotoSource  # noqa: E402
from app.services import photo_service, photo_thumb  # noqa: E402

settings.ensure_directories()
init_db()
Base.metadata.create_all(bind=engine)

PASS = 0
FAIL = 0
HAS_FFMPEG = bool(resolve_binaries().ffmpeg)


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}  {detail}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def make_jpeg(path: Path, w=640, h=480) -> bool:
    if not HAS_FFMPEG:
        return False
    ffmpeg = resolve_binaries().ffmpeg
    res = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=blue:s={w}x{h}:d=0.1",
            "-frames:v",
            "1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return res.returncode == 0 and path.is_file() and path.stat().st_size > 0


dest = photo_thumb.thumb_abs(12)
check(
    "缩略图落在 derived/photo-thumbs",
    dest.parent == settings.photo_thumb_dir.resolve() or dest.parent == settings.photo_thumb_dir,
    str(dest),
)
try:
    dest.resolve().relative_to(settings.derived_dir.resolve())
    confined = True
except ValueError:
    confined = False
check("缩略图路径不越出 derived", confined, str(dest))

photo_thumb.remove_thumb(12)
check("删除不存在的缩略图不报错", not dest.exists())

folder = _tmp / "pics"
folder.mkdir()
src = folder / "wide.jpg"
if not make_jpeg(src):
    check("ffmpeg 可用才能测生成", False, "未检测到 ffmpeg，跳过生成断言")
else:
    db = SessionLocal()
    artist = Artist(name="Thumb Tester")
    db.add(artist)
    db.commit()
    db.refresh(artist)
    aid = artist.id
    db.close()

    db = SessionLocal()
    source = photo_service.bind_source(
        db,
        owner_type="artist",
        owner_id=aid,
        section="official",
        folder_path=str(folder),
    )
    sid = source.id
    db.close()

    db = SessionLocal()
    source = db.get(PhotoSource, sid)
    stats = photo_service.scan_source(db, source)
    check("扫描入队缩略图", stats.thumbs_queued >= 1, str(stats.thumbs_queued))
    db.close()

    db = SessionLocal()
    photo = db.scalar(
        select(Photo)
        .options(selectinload(Photo.source))
        .where(Photo.source_id == sid, Photo.active_filter())
    )
    before = src.stat().st_size
    thumb = photo_thumb.generate_thumbnail(photo)
    check("生成缩略图文件", thumb.is_file() and thumb.stat().st_size > 0, str(thumb))
    check(
        "缩略图小于原图",
        thumb.stat().st_size < before,
        f"thumb={thumb.stat().st_size} src={before}",
    )
    check("原文件未被改动", src.stat().st_size == before)
    try:
        thumb.resolve().relative_to(settings.derived_dir.resolve())
        in_derived = True
    except ValueError:
        in_derived = False
    check("生成结果在 derived 内", in_derived)
    pid = photo.id
    db.close()

    again = photo_thumb.thumb_abs(pid)
    mtime1 = again.stat().st_mtime
    db = SessionLocal()
    photo = db.scalar(
        select(Photo).options(selectinload(Photo.source)).where(Photo.id == pid)
    )
    photo_thumb.generate_thumbnail(photo)
    db.close()
    check("已有缩略图则跳过重做", again.stat().st_mtime == mtime1)

    photo_thumb._queue.join()
    db = SessionLocal()
    source = db.get(PhotoSource, sid)
    photo_service.delete_source(db, source)
    db.close()
    check("解绑后删除缩略图", not photo_thumb.thumb_abs(pid).exists())

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
