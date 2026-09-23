"""失效视频清理：文件已删的库记录应被软删除；空库目录不可误删全库。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="lib_cleanup_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.models.music_video import MusicVideo
from app.services.library_service import (
    _rel_key,
    cleanup_missing_videos,
    maybe_cleanup_missing_videos,
    resolve_video_abs_path,
)
from sqlalchemy import select

settings.ensure_directories()
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


def add_mv(**kwargs) -> MusicVideo:
    db = SessionLocal()
    try:
        mv = MusicVideo(
            name=kwargs.pop("name", "clip"),
            ingestion_status=kwargs.pop("ingestion_status", "library"),
            **kwargs,
        )
        db.add(mv)
        db.commit()
        db.refresh(mv)
        return mv
    finally:
        db.close()


def get_mv(mv_id: int) -> MusicVideo:
    db = SessionLocal()
    try:
        return db.get(MusicVideo, mv_id)
    finally:
        db.close()


def run_cleanup(**kwargs):
    db = SessionLocal()
    try:
        return cleanup_missing_videos(db, **kwargs)
    finally:
        db.close()


# ----- 1. 相对路径：文件还在则保留，文件没了则软删除 -----
keep_file = settings.library_dir / "keep.mp4"
keep_file.write_bytes(b"keep")
gone_rel = "gone.mp4"
keep_mv = add_mv(name="keep", file_path="keep.mp4", ingestion_status="library")
gone_mv = add_mv(name="gone", file_path=gone_rel, ingestion_status="library")
empty_mv = add_mv(name="empty", file_path=None, ingestion_status="library")

result = run_cleanup()
check("清理计数", result["cleaned"] == 2, str(result))
check("缺失列表含 gone", any(i["id"] == gone_mv.id for i in result["items"]))
check("缺失列表含 empty", any(i["id"] == empty_mv.id for i in result["items"]))
check("keep 仍活跃", get_mv(keep_mv.id).deleted_at is None)
check("gone 已软删除", get_mv(gone_mv.id).deleted_at is not None)
check("empty 已软删除", get_mv(empty_mv.id).deleted_at is not None)
check("未触发挂载防护", result["skipped_unmounted"] is False)


# ----- 2. 已软删除的不再处理 -----
result2 = run_cleanup()
check("二次扫描不再清理", result2["cleaned"] == 0 and result2["missing"] == 0, str(result2))


# ----- 3. 缩略图与转码缓存一并清掉 -----
thumb = settings.thumbnail_dir / "willgone.webp"
thumb.parent.mkdir(parents=True, exist_ok=True)
thumb.write_bytes(b"thumb")
cache_dir = settings.playback_transcode_dir / "99999" / "main" / "w0"
cache_dir.mkdir(parents=True, exist_ok=True)
(cache_dir / "seg.ts").write_bytes(b"ts")
sidecar = settings.library_dir / "orphan.info.json"
# 视频不存在，但同名 json 还在
derived_mv = add_mv(
    name="derived",
    file_path="orphan.mp4",
    ingestion_status="library",
    thumbnail_path="thumbnails/willgone.webp",
)
# 需要让 purge_cache_for 对得上 id
# 上面 id 不是 99999；改成按 derived_mv.id 建缓存
real_cache = settings.playback_transcode_dir / str(derived_mv.id) / "main" / "w0"
real_cache.mkdir(parents=True, exist_ok=True)
(real_cache / "seg.ts").write_bytes(b"ts")

result3 = run_cleanup()
check("派生记录被软删除", result3["cleaned"] == 1, str(result3))
check("默认不删缩略图", thumb.exists())
check("默认不删转码缓存", real_cache.exists())
check("默认 files_purged=0", result3.get("files_purged", 0) == 0, str(result3))

purge_thumb = settings.thumbnail_dir / "willpurge.webp"
purge_thumb.write_bytes(b"thumb")
purge_side = settings.library_dir / "purge_me.info.json"
purge_side.write_text("{}")
purge_mv = add_mv(
    name="purge",
    file_path="purge_me.mp4",
    ingestion_status="library",
    thumbnail_path="thumbnails/willpurge.webp",
)
result3b = run_cleanup(purge_files=True)
check("确认后软删除", result3b["cleaned"] == 1, str(result3b))
check("确认后删缩略图", not purge_thumb.exists())
check("视频不存在时才删 sidecar json", not purge_side.exists())

keep_side = settings.library_dir / "keep.info.json"
keep_side.write_text("{}")
result3c = run_cleanup(purge_files=True)
check("视频还在时不删旁边 json", keep_side.exists(), str(result3c))
keep_side.unlink(missing_ok=True)


# ----- 4. incoming 绝对路径：文件删除后清理 -----
abs_src = settings.incoming_dir / "abs_clip.mkv"
abs_src.write_bytes(b"incoming")
abs_mv = add_mv(
    name="abs",
    file_path=str(abs_src),
    ingestion_status="incoming",
)
abs_src.unlink()
result4 = run_cleanup()
check("incoming 绝对路径失效被清理", result4["cleaned"] == 1, str(result4))
check("incoming 记录已软删除", get_mv(abs_mv.id).deleted_at is not None)


# ----- 5. 挂载防护：正式库空目录 + 仍有相对路径记录 → 不删 -----
db = SessionLocal()
try:
    for row in db.scalars(select(MusicVideo).where(MusicVideo.deleted_at.is_(None))).all():
        row.soft_delete()
    db.commit()
finally:
    db.close()

keep_file.unlink(missing_ok=True)
for p in settings.library_dir.rglob("*"):
    if p.is_file():
        p.unlink()
for p in sorted(settings.library_dir.rglob("*"), reverse=True):
    if p.is_dir():
        p.rmdir()

guard_mv = add_mv(name="guard", file_path="still.mp4", ingestion_status="library")
result5 = run_cleanup()
check(
    "空库触发挂载防护",
    result5["skipped_unmounted"] is True and result5["cleaned"] == 0,
    str(result5),
)
check("防护下记录仍活跃", get_mv(guard_mv.id).deleted_at is None)

result6 = run_cleanup(respect_mount_guard=False)
check("force 忽略防护并清理", result6["cleaned"] == 1, str(result6))
check("force 后记录软删除", get_mv(guard_mv.id).deleted_at is not None)

# 半挂载：目录里只有 .DS_Store，没有视频，不得把记录当缺失
db = SessionLocal()
try:
    for row in db.scalars(select(MusicVideo).where(MusicVideo.deleted_at.is_(None))).all():
        row.soft_delete()
    db.commit()
finally:
    db.close()
(settings.library_dir / ".DS_Store").write_bytes(b"junk")
ghost_mv = add_mv(name="ghost", file_path="Twice/clip.mp4", ingestion_status="library")
result5b = run_cleanup()
check(
    "仅有 .DS_Store 仍触发挂载防护",
    result5b["skipped_unmounted"] is True and result5b["cleaned"] == 0,
    str(result5b),
)
check("半挂载下记录仍活跃", get_mv(ghost_mv.id).deleted_at is None)
(settings.library_dir / ".DS_Store").unlink(missing_ok=True)
db = SessionLocal()
try:
    row = db.get(MusicVideo, ghost_mv.id)
    if row and row.deleted_at is None:
        row.soft_delete()
        db.commit()
finally:
    db.close()


# ----- 6. dry_run 不写库 -----
keep_file.write_bytes(b"keep")
dry_mv = add_mv(name="dry", file_path="dry.mp4", ingestion_status="library")
result7 = run_cleanup(dry_run=True)
check("dry_run 报告缺失", result7["missing"] == 1 and result7["cleaned"] == 0, str(result7))
check("dry_run 不软删除", get_mv(dry_mv.id).deleted_at is None)


# ----- 7. resolve 路径 -----
db = SessionLocal()
try:
    mv = db.scalar(select(MusicVideo).where(MusicVideo.id == keep_mv.id))
    resolved = resolve_video_abs_path(mv)
    check(
        "resolve 相对路径",
        resolved is not None and resolved == settings.library_dir / "keep.mp4",
        str(resolved),
    )
finally:
    db.close()


check("_rel_key 不吞掉隐藏目录点", _rel_key(".hidden/a.mp4") == ".hidden/a.mp4")
# Windows 上 _rel_key 会 casefold，故与参照值一起归一化后比较（验证的是「./ 被去掉」）
check("_rel_key 去掉 ./ 前缀", _rel_key("./Twice/a.mp4") == _rel_key("Twice/a.mp4"))
check("自动挂钩不再写库", maybe_cleanup_missing_videos(SessionLocal()) is None)

print()
print(f"{PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
