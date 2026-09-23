"""照片墙手动上传：写入独立上传目录并建索引。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="photo_up_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")
os.environ["PHOTO_BROWSE_ROOTS"] = str(_tmp)

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.photo import Photo  # noqa: E402
from app.services import photo_service  # noqa: E402

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
artist = Artist(name="Uploader")
db.add(artist)
db.commit()
db.refresh(artist)
aid = artist.id
db.close()

db = SessionLocal()
src = photo_service.ensure_upload_source(db, "artist", aid)
src2 = photo_service.ensure_upload_source(db, "artist", aid)
check("上传来源只建一次", src.id == src2.id)
check("分区是照片墙", src.section == "wall")
check("目录在 derived/photo-uploads 下", "photo-uploads" in src.folder_path)
p1 = photo_service.add_uploaded_photo(db, src, original_name="a.jpg", data=b"jpeg-bytes")
p2 = photo_service.add_uploaded_photo(db, src, original_name="a.jpg", data=b"jpeg-bytes-2")
check("同名不覆盖", p1.file_name != p2.file_name, f"{p1.file_name} / {p2.file_name}")
check("文件写到磁盘", Path(p1.file_path).is_file())
check("独立 uid", bool(p1.uid and p2.uid and p1.uid != p2.uid))
feed = photo_service.owner_feed(
    db, owner_type="artist", owner_id=aid, section="wall", page=1, page_size=24
)
check("照片墙能看到上传", feed["photo_count"] == 2, str(feed["photo_count"]))
try:
    photo_service.add_uploaded_photo(db, src, original_name="x.exe", data=b"nope")
    check("拒绝非媒体", False)
except photo_service.PhotoServiceError:
    check("拒绝非媒体", True)
db.close()

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
