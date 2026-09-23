"""收藏夹按照片 uid 收藏；一张图可进多个夹。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="photo_col_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")
os.environ["PHOTO_BROWSE_ROOTS"] = str(_tmp)

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.photo import Photo, PhotoCollection, PhotoSource  # noqa: E402
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


folder = _tmp / "pics"
folder.mkdir()
(folder / "a.jpg").write_bytes(b"a")
(folder / "b.jpg").write_bytes(b"b")

db = SessionLocal()
artist = Artist(name="Collector")
db.add(artist)
db.commit()
db.refresh(artist)
aid = artist.id
db.close()

db = SessionLocal()
src = photo_service.bind_source(
    db, owner_type="artist", owner_id=aid, section="wall", folder_path=str(folder)
)
photo_service.scan_source(db, src)
photos = list(
    db.scalars(select(Photo).where(Photo.source_id == src.id, Photo.active_filter())).all()
)
check("扫描到两张图", len(photos) == 2, str(len(photos)))
p1, p2 = photos[0], photos[1]
uid1, uid2 = p1.uid, p2.uid
id1, id2 = p1.id, p2.id
db.close()

db = SessionLocal()
try:
    photo_collection.create_collection(db, name="  ")
    check("空名称拒绝", False)
except photo_service.PhotoServiceError:
    check("空名称拒绝", True)
c1 = photo_collection.create_collection(db, name="鞋子")
c2 = photo_collection.create_collection(db, name="丝袜")
check("收藏夹有 uid", bool(c1.uid and c2.uid))
cid1, cid2 = c1.id, c2.id
db.close()

db = SessionLocal()
c1 = photo_collection.get_collection(db, cid1)
photo = Photo.get_by_uid(db, uid1)
photo_collection.add_photo(db, c1, photo)
photo_collection.add_photo(db, c1, photo)
c2 = photo_collection.get_collection(db, cid2)
photo_collection.add_photo(db, c2, photo)
photo2 = Photo.get_active(db, id2)
photo_collection.add_photo(db, c2, photo2)
db.close()

db = SessionLocal()
ms = photo_collection.memberships_for_photos(db, [id1, id2])
check("同一张图可进两个夹", set(ms[id1]) == {cid1, cid2}, str(ms[id1]))
check("第二张只在一个夹", ms[id2] == [cid2], str(ms[id2]))
c1 = photo_collection.get_collection(db, cid1)
feed = photo_collection.collection_photos(db, c1, page=1, page_size=24)
check("鞋子夹 1 张", feed["photo_count"] == 1, str(feed["photo_count"]))
c2 = photo_collection.get_collection(db, cid2)
feed = photo_collection.collection_photos(db, c2, page=1, page_size=24)
check("丝袜夹 2 张", feed["photo_count"] == 2, str(feed["photo_count"]))
info = photo_collection.collection_to_read(db, c1)
check("封面取最新加入的照片", info["cover_photo_id"] == id1, str(info["cover_photo_id"]))
db.close()

db = SessionLocal()
c1 = photo_collection.get_collection(db, cid1)
photo = Photo.get_by_uid(db, uid1)
removed = photo_collection.remove_photo(db, c1, photo)
check("从鞋子夹移出", removed)
ms = photo_collection.memberships_for_photos(db, [id1])
check("移出后仍在丝袜夹", ms[id1] == [cid2], str(ms[id1]))
db.close()

db = SessionLocal()
p = db.get(Photo, id1)
p.soft_delete()
db.commit()
c2 = photo_collection.get_collection(db, cid2)
feed = photo_collection.collection_photos(db, c2, page=1, page_size=24)
check("软删照片不出现在夹里", feed["photo_count"] == 1, str(feed["photo_count"]))
p = db.get(Photo, id1)
p.restore()
db.commit()
feed = photo_collection.collection_photos(db, c2, page=1, page_size=24)
check("恢复后重新出现", feed["photo_count"] == 2, str(feed["photo_count"]))
db.close()

db = SessionLocal()
c1 = photo_collection.get_collection(db, cid1)
photo_collection.delete_collection(db, c1)
gone = PhotoCollection.get_active(db, cid1)
check("软删收藏夹后不可见", gone is None)
listed = photo_collection.list_collections(db)
check("列表不含已删夹", all(c.id != cid1 for c in listed), str([c.id for c in listed]))
db.close()

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
