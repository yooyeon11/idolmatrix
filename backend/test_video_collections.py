"""视频收藏夹：按 uid 收藏，一个视频可进多个夹。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="video_col_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.models.video_collection import VideoCollection  # noqa: E402
from app.services import video_collection  # noqa: E402
from app.services.photo_service import PhotoServiceError  # noqa: E402

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
v1 = MusicVideo(name="Clip A", ingestion_status="library", video_type="OfficialMV")
v2 = MusicVideo(name="Clip B", ingestion_status="library", video_type="PersonalFancam")
db.add_all([v1, v2])
db.commit()
db.refresh(v1)
db.refresh(v2)
uid1, uid2 = v1.uid, v2.uid
id1, id2 = v1.id, v2.id
db.close()

db = SessionLocal()
try:
    video_collection.create_collection(db, name="  ")
    check("空名称拒绝", False)
except PhotoServiceError:
    check("空名称拒绝", True)
c1 = video_collection.create_collection(db, name="舞台")
c2 = video_collection.create_collection(db, name="直拍")
check("收藏夹有 uid", bool(c1.uid and c2.uid))
cid1, cid2 = c1.id, c2.id
db.close()

db = SessionLocal()
c1 = video_collection.get_collection(db, cid1)
video = MusicVideo.get_by_uid(db, uid1)
video_collection.add_video(db, c1, video)
video_collection.add_video(db, c1, video)
c2 = video_collection.get_collection(db, cid2)
video_collection.add_video(db, c2, video)
video2 = MusicVideo.get_active(db, id2)
video_collection.add_video(db, c2, video2)
db.close()

db = SessionLocal()
ms = video_collection.memberships_for_videos(db, [id1, id2])
check("同一视频可进两个夹", sorted(ms[id1]) == [cid1, cid2], str(ms[id1]))
check("第二支只在一个夹", ms[id2] == [cid2], str(ms[id2]))
stage = video_collection.get_collection(db, cid1)
items, total = video_collection.collection_videos(db, stage, page=1, page_size=24)
check("舞台夹 1 个", total == 1, str(total))
fancam = video_collection.get_collection(db, cid2)
items, total = video_collection.collection_videos(db, fancam, page=1, page_size=24)
check("直拍夹 2 个", total == 2, str(total))
cover = video_collection.collection_to_read(db, fancam)["cover_video_id"]
check("封面取最新加入", cover == id2, str(cover))
db.close()

db = SessionLocal()
c1 = video_collection.get_collection(db, cid1)
video = MusicVideo.get_active(db, id1)
video_collection.remove_video(db, c1, video)
ms = video_collection.memberships_for_videos(db, [id1])
check("从舞台夹移出", cid1 not in ms[id1])
check("移出后仍在直拍夹", cid2 in ms[id1], str(ms[id1]))
db.close()

db = SessionLocal()
v1 = MusicVideo.get_active(db, id1)
v1.soft_delete()
db.commit()
c2 = video_collection.get_collection(db, cid2)
_items, total = video_collection.collection_videos(db, c2, page=1, page_size=24)
check("软删视频不出现在夹里", total == 1, str(total))
v1.restore()
db.commit()
_items, total = video_collection.collection_videos(db, c2, page=1, page_size=24)
check("恢复后重新出现", total == 2, str(total))
c1 = video_collection.get_collection(db, cid1)
video_collection.delete_collection(db, c1)
try:
    video_collection.get_collection(db, cid1)
    check("软删收藏夹后不可见", False)
except PhotoServiceError:
    check("软删收藏夹后不可见", True)
listed = [c.id for c in video_collection.list_collections(db)]
check("列表不含已删夹", listed == [cid2], str(listed))
db.close()

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
