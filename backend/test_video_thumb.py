"""换源文件后封面必须重做，不能继续用旧 id 缩略图。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="vthumb_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.services.video_thumb import ensure_video_thumbnail, read_stamp  # noqa: E402

settings.ensure_directories()
init_db()
Base.metadata.create_all(bind=engine)

PASS = 0
FAIL = 0
JPEG_A = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xd9"
)
JPEG_B = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xfe\x00\x0eold-cover-xx\xff\xd9"
)


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}  {detail}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


lib = settings.library_dir
lib.mkdir(parents=True, exist_ok=True)
video = lib / "clip.mp4"
video.write_bytes(b"fake-mp4-old")
cover = lib / "clip.jpg"
cover.write_bytes(JPEG_A)

db = SessionLocal()
mv = MusicVideo(
    name="clip",
    ingestion_status="library",
    video_type="OfficialMV",
    file_path="clip.mp4",
    file_name="clip.mp4",
)
db.add(mv)
db.commit()
db.refresh(mv)
mid = mv.id

thumb1 = ensure_video_thumbnail(db, mv)
check("首次用 sidecar 生成封面", thumb1 is not None and thumb1.is_file())
check("登记了 thumbnail_path", bool(mv.thumbnail_path))
old_bytes = thumb1.read_bytes()
old_stamp = read_stamp(mid)
check("写下源指纹", bool(old_stamp))

same = ensure_video_thumbnail(db, mv)
check("源没变不重做", same is not None and read_stamp(mid) == old_stamp)

cover.write_bytes(JPEG_B)
thumb2 = ensure_video_thumbnail(db, mv)
check("sidecar 变了会重做", thumb2 is not None and thumb2.read_bytes() != old_bytes)
check("指纹已更新", read_stamp(mid) != old_stamp, str(read_stamp(mid)))

# 模拟 NAS 重部署：旧 derived 封面还在，视频文件换成新的且没有 sidecar
cover.unlink()
stale = settings.thumbnail_dir / f"{mid}.jpg"
stale.write_bytes(JPEG_A)
video.write_bytes(b"fake-mp4-new-version-xxxxxxxxxxx")
# 清掉 stamp，等同于拷了旧封面、没拷 .src
(settings.thumbnail_dir / f"{mid}.src").unlink(missing_ok=True)
before = stale.read_bytes()
thumb3 = ensure_video_thumbnail(db, mv)
# 没有 sidecar 时会尝试从视频截帧；假 mp4 会失败，应保留旧文件而不是 500
check("截帧失败时不把旧封面删掉", thumb3 is not None and thumb3.is_file())
db.close()


# ===== 手动选帧封面：cover_manual=True 时 sidecar/指纹变化不再覆盖 =====
_tmp2 = Path(tempfile.mkdtemp(prefix="vthumb_manual_"))
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp2 / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp2 / "library")
settings.thumbnail_dir.mkdir(parents=True, exist_ok=True)
lib2 = settings.library_dir
lib2.mkdir(parents=True, exist_ok=True)
video2 = lib2 / "clip.mp4"
video2.write_bytes(b"fake-mp4-v1")
cover2 = lib2 / "clip.jpg"
cover2.write_bytes(JPEG_A)

from app.services.video_thumb import (  # noqa: E402
    clear_cover_frames,
    clear_manual_cover,
    write_stamp,
)

db2 = SessionLocal()
mv2 = MusicVideo(
    name="clip2",
    ingestion_status="library",
    video_type="OfficialMV",
    file_path="clip.mp4",
    file_name="clip.mp4",
)
db2.add(mv2)
db2.commit()
db2.refresh(mv2)
mid2 = mv2.id

# 先走自动流程拿到 sidecar 封面
auto = ensure_video_thumbnail(db2, mv2)
check("manual: 自动封面生成", auto is not None and auto.is_file())

# 模拟手动封面：写死一个 jpg + cover_manual=True + stamp=manual
manual_rel = Path("thumbnails") / f"{mid2}.jpg"
manual_file = settings.derived_dir / manual_rel
manual_file.parent.mkdir(parents=True, exist_ok=True)
manual_file.write_bytes(JPEG_B)
mv2.thumbnail_path = manual_rel.as_posix()
mv2.cover_manual = True
write_stamp(mid2, "manual")
db2.commit()

# sidecar 变了 + 视频文件变了：都不得覆盖手动封面
cover2.write_bytes(JPEG_A + b"-changed")
video2.write_bytes(b"fake-mp4-v2-changed")
kept = ensure_video_thumbnail(db2, mv2)
check(
    "manual: sidecar/指纹变化不覆盖手动封面",
    kept is not None and kept.read_bytes() == JPEG_B,
)
check("manual: 标记保持", mv2.cover_manual is True)

# 手动封面文件丢失：清除标记并走自动流程
manual_file.unlink()
recovered = ensure_video_thumbnail(db2, mv2)
check(
    "manual: 文件丢失时降级自动重做",
    recovered is not None and recovered.is_file() and mv2.cover_manual is False,
)

# 清除手动封面：恢复 sidecar 优先
manual_file.write_bytes(JPEG_B)
mv2.cover_manual = True
mv2.thumbnail_path = manual_rel.as_posix()
write_stamp(mid2, "manual")
db2.commit()
clear_manual_cover(db2, mv2)
after_clear = ensure_video_thumbnail(db2, mv2)
check(
    "manual: 清除后恢复 sidecar 封面",
    after_clear is not None and after_clear.read_bytes() == (JPEG_A + b"-changed"),
)
check("manual: 候选帧目录已清理", not (settings.derived_dir / "cover-frames" / str(mid2)).exists())

clear_cover_frames(mid2)
db2.close()

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
