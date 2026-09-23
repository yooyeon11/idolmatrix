"""从图库裁切头像/横幅：坐标归一、比例钳制。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="portrait_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")
os.environ["PHOTO_BROWSE_ROOTS"] = str(_tmp)

from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.photo import Photo, PhotoSource  # noqa: E402
from app.services.portrait_service import (  # noqa: E402
    BANNER_RATIO,
    apply_photo_crop,
    normalize_crop,
)

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


print("== 裁剪框 ==")
x, y, w, h = normalize_crop(400, 400, 0.0, 0.0, 1.0, 1.0, 1.0)
check("1:1 整图", w == h and w >= 2, (x, y, w, h))
x, y, w, h = normalize_crop(800, 400, 0.0, 0.0, 1.0, 1.0, BANNER_RATIO)
check(f"边框 {BANNER_RATIO}:1 宽图用满", abs(w / h - BANNER_RATIO) < 0.05 and y == 0, (x, y, w, h))
x, y, w, h = normalize_crop(400, 800, 0, 0, 1, 1, BANNER_RATIO)
check(f"边框 {BANNER_RATIO}:1 竖图取中间带", abs(w / h - BANNER_RATIO) < 0.05 and w == 400, (x, y, w, h))
x, y, w, h = normalize_crop(1000, 1000, 0.1, 0.1, 0.4, 0.4, 1.0)
check("给定 1:1 区域保留", abs(w / h - 1) < 0.02 and w >= 390, (x, y, w, h))

print("== 落盘（需 ffmpeg）==")
import shutil  # noqa: E402

from app.core.ffmpeg import FFmpegNotFoundError, require_ffmpeg, require_ffprobe  # noqa: E402

ffmpeg = shutil.which("ffmpeg")
try:
    require_ffmpeg()
    require_ffprobe()
    ff_ok = True
except FFmpegNotFoundError:
    ff_ok = False
if not ffmpeg or not ff_ok:
    print("  SKIP  本机没有可用 ffmpeg/ffprobe，跳过落盘")
else:
    import subprocess  # noqa: E402

    src_dir = _tmp / "incoming"
    src_dir.mkdir(parents=True, exist_ok=True)
    jpg = src_dir / "shot.jpg"
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=blue:s=800x600", "-frames:v", "1", str(jpg)],
        check=True,
        capture_output=True,
    )
    db = SessionLocal()
    artist = Artist(name="Crop Tester")
    db.add(artist)
    db.commit()
    db.refresh(artist)
    source = PhotoSource(
        owner_type="artist",
        owner_id=artist.id,
        section="official",
        folder_path=str(src_dir),
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    photo = Photo(
        source_id=source.id,
        section="official",
        file_path=str(jpg),
        file_name="shot.jpg",
        media_kind="image",
    )
    db.add(photo)
    db.commit()
    db.refresh(photo)
    pid = photo.id
    aid = artist.id
    db.close()

    db = SessionLocal()
    av = apply_photo_crop(db, pid, kind="avatar", x=0.1, y=0.1, width=0.5, height=0.5)
    # 候选文件走唯一命名（{owner_id}_{序号}.jpg），只要落在该实体的目录下即算对
    check("头像路径 avatars/artists", av["path"].startswith(f"avatars/artists/{aid}"), av["path"])
    av_file = settings.derived_dir / av["path"]
    check("头像文件已写", av_file.is_file() and av_file.stat().st_size > 0, str(av_file))
    bn = apply_photo_crop(db, pid, kind="banner", x=0.0, y=0.2, width=1.0, height=0.5)
    check("横幅路径 banners/artists", bn["path"].startswith(f"banners/artists/{aid}"), bn["path"])
    bn_file = settings.derived_dir / bn["path"]
    check("横幅文件已写", bn_file.is_file() and bn_file.stat().st_size > 0)
    from app.media.ffprobe import probe  # noqa: E402

    info = probe(bn_file)
    ratio = (info.width or 0) / (info.height or 1)
    check(f"横幅落盘比例 {BANNER_RATIO}:1", abs(ratio - BANNER_RATIO) < 0.01, f"{info.width}x{info.height}")
    row = db.get(Artist, aid)
    check("artist.avatar_path", row is not None and row.avatar_path == av["path"])
    check("artist.banner_path", row is not None and row.banner_path == bn["path"])
    db.close()

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
