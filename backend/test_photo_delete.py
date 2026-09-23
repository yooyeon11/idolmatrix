"""彻底删除照片 / 帖子：磁盘文件 + 索引，扫描不再恢复。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="photo_delete_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")
os.environ["PHOTO_BROWSE_ROOTS"] = str(_tmp)

from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.models.artist import Artist  # noqa: E402
from app.models.photo import Photo, PhotoCollection, PhotoCollectionItem, PhotoSource  # noqa: E402
from app.services import photo_collection, photo_service  # noqa: E402

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


def write_txt(path: Path, published: str, caption: str, files: list[str]) -> None:
    body = (
        f"发布时间：{published}\n\n"
        f"博文原文：{caption}\n\n"
        "媒体文件列表：\n"
        + "\n".join(files)
        + "\n"
    )
    path.write_text(body, encoding="utf-8")


folder = _tmp / "ig"
folder.mkdir()
names = [
    "2023-05-17 00-46-45_01.jpg",
    "2023-05-17 00-46-45_02.jpg",
    "2023-05-17 00-46-45_03.jpg",
]
write_txt(folder / "2023-05-17 00-46-45.txt", "2023-05-17 00:46:45", "hello", names)
for name in names:
    (folder / name).write_bytes(b"fake-jpg")
(folder / "loose.jpg").write_bytes(b"loose")

db = SessionLocal()
artist = Artist(name="DELETE-TEST")
db.add(artist)
db.commit()
db.refresh(artist)
aid = artist.id
db.close()

db = SessionLocal()
src = photo_service.bind_source(
    db,
    owner_type="artist",
    owner_id=aid,
    section="official",
    folder_path=str(folder),
)
sid = src.id
photo_service.scan_source(db, src)
db.close()

db = SessionLocal()
photos = list(
    db.scalars(
        select(Photo).where(Photo.source_id == sid, Photo.active_filter()).order_by(Photo.file_name)
    ).all()
)
by_name = {p.file_name: p for p in photos}
one = by_name["2023-05-17 00-46-45_02.jpg"]
col = PhotoCollection(name="keep")
db.add(col)
db.flush()
photo_collection.add_photo(db, col, one)
cid = col.id
db.commit()
db.close()

db = SessionLocal()
one = db.scalar(
    select(Photo)
    .options(selectinload(Photo.source))
    .where(Photo.file_name == "2023-05-17 00-46-45_02.jpg")
)
r = photo_service.delete_photo(db, one)
db.close()
check("单张 deleted=1", r["deleted"] == 1, str(r))
check("单张文件已从磁盘删除", not (folder / "2023-05-17 00-46-45_02.jpg").exists())
check("帖子原文仍在（帖内还有图）", (folder / "2023-05-17 00-46-45.txt").is_file())
check("另两张还在", (folder / names[0]).is_file() and (folder / names[2]).is_file())

db = SessionLocal()
left = list(db.scalars(select(Photo).where(Photo.source_id == sid, Photo.active_filter())).all())
check("索引去掉这一张", len(left) == 3, str(len(left)))  # 2 remaining in post + loose
check("收藏夹条目随照片级联删除", db.scalar(select(PhotoCollectionItem).where(PhotoCollectionItem.collection_id == cid)) is None)
db.close()

db = SessionLocal()
source = db.get(PhotoSource, sid)
stats = photo_service.scan_source(db, source)
check("再扫描不会恢复已删文件", stats.restored == 0 and stats.created == 0, f"restored={stats.restored} created={stats.created}")
db.close()

db = SessionLocal()
remain = db.scalar(
    select(Photo)
    .options(selectinload(Photo.source))
    .where(Photo.file_name == "2023-05-17 00-46-45_01.jpg", Photo.active_filter())
)
r2 = photo_service.delete_post(db, remain)
db.close()
check("删帖 deleted=2", r2["deleted"] == 2, str(r2))
check("帖内文件都删了", not (folder / names[0]).exists() and not (folder / names[2]).exists())
check("sidecar 原文已删", not (folder / "2023-05-17 00-46-45.txt").exists())
check("散图还在", (folder / "loose.jpg").is_file())

db = SessionLocal()
left = list(db.scalars(select(Photo).where(Photo.source_id == sid, Photo.active_filter())).all())
check("只剩散图", len(left) == 1 and left[0].file_name == "loose.jpg", str([p.file_name for p in left]))
loose = db.scalar(
    select(Photo).options(selectinload(Photo.source)).where(Photo.file_name == "loose.jpg")
)
r3 = photo_service.delete_photo(db, loose)
db.close()
check("散图删除", r3["deleted"] == 1 and not (folder / "loose.jpg").exists(), str(r3))

print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
