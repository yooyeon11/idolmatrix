"""文件夹浏览聚合 + 软删除恢复。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="folder_recycle_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.services.library_service import aggregate_folder_browse  # noqa: E402

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


rows = [
    (1, "Hearts2Hearts/Lemon Tang/Jiwoo/a.mkv"),
    (2, "Hearts2Hearts/Lemon Tang/Jiwoo/b.mkv"),
    (3, "Hearts2Hearts/RUDE!/Yuha/c.mkv"),
    (4, "root.mkv"),
    (5, "RedVelvet/d.mkv"),
]
folders, vids = aggregate_folder_browse(rows, "")
names = [f["name"] for f in folders]
check("根目录子文件夹", names == ["Hearts2Hearts", "RedVelvet"], str(names))
check("根目录文件计入当前层", vids == [4], str(vids))
h2h = next(f for f in folders if f["name"] == "Hearts2Hearts")
check("组合文件夹计数含全部子孙", h2h["count"] == 3, str(h2h))
check("封面取最新一条", h2h["cover_id"] == 1, str(h2h["cover_id"]))

folders, vids = aggregate_folder_browse(rows, "Hearts2Hearts")
names = [f["name"] for f in folders]
check("进入组合后子文件夹", names == ["Lemon Tang", "RUDE!"], str(names))
check("组合目录本身无文件", vids == [], str(vids))

folders, vids = aggregate_folder_browse(rows, "Hearts2Hearts/Lemon Tang/Jiwoo")
check("最底层无子文件夹", folders == [], str(folders))
check("最底层列出视频", vids == [1, 2], str(vids))


def add_mv(**kwargs) -> MusicVideo:
    db = SessionLocal()
    try:
        mv = MusicVideo(name=kwargs.pop("name", "clip"), ingestion_status="library", **kwargs)
        db.add(mv)
        db.commit()
        db.refresh(mv)
        return mv
    finally:
        db.close()


def add_artist(name: str) -> Artist:
    db = SessionLocal()
    try:
        a = Artist(name=name)
        db.add(a)
        db.commit()
        db.refresh(a)
        return a
    finally:
        db.close()


a = add_artist("Irene")
db = SessionLocal()
try:
    obj = db.get(Artist, a.id)
    obj.soft_delete()
    db.commit()
    db.refresh(obj)
    check("软删除写入 deleted_at", obj.deleted_at is not None)
    obj.restore()
    db.commit()
    db.refresh(obj)
    check("恢复后 deleted_at 为空", obj.deleted_at is None)
finally:
    db.close()

gone = add_mv(name="gone", file_hash="abc123")
keep = None
db = SessionLocal()
try:
    obj = db.get(MusicVideo, gone.id)
    obj.soft_delete()
    db.commit()
finally:
    db.close()
keep = add_mv(name="keep", file_hash="abc123")
db = SessionLocal()
try:
    obj = db.get(MusicVideo, gone.id)
    obj.restore()
    raised = False
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raised = True
    check("恢复撞活跃 file_hash 会失败", raised)
finally:
    db.close()
_ = keep

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
