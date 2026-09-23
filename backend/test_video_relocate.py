"""已入库视频更改存储路径：规则推荐 + 文件移动（伴随文件 / 冲突后缀 / 越界防护）。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="video_relocate_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.models.group import Group
from app.models.music_video import MusicVideo
from app.models.song import Song
from app.models.task import FileMoveLog
from app.services.library_service import (
    relocate_video_file,
    suggest_relocate_path,
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


def expect_error(name, fn, keyword):
    try:
        fn()
        check(name, False, "未抛出异常")
    except (ValueError, FileNotFoundError) as e:
        check(name, keyword in str(e), str(e))


def make_library_video(name, file_path, *, group_name=None, song_name=None, **kw):
    """创建已入库视频（可选关联组合/歌曲），返回 {"id": ...}。"""
    kw.setdefault("ingestion_status", "library")
    # 与真实入库同口径：主类型显式写入（= video_types[0]），
    # 避免落进列默认值 Other 被合并进类型集合
    if kw.get("video_types") and not kw.get("video_type"):
        kw["video_type"] = kw["video_types"][0]
    db = SessionLocal()
    try:
        mv = MusicVideo(
            name=name,
            file_path=file_path,
            **kw,
        )
        if group_name:
            mv.groups.append(Group(name=group_name))
        if song_name:
            mv.songs.append(Song(name=song_name))
        db.add(mv)
        db.commit()
        db.refresh(mv)
        data = {"id": mv.id}
        return data
    finally:
        db.close()


def get_mv(mv_id: int):
    db = SessionLocal()
    try:
        return db.get(MusicVideo, mv_id)
    finally:
        db.close()


def relocate(mv_id: int, destination_rel=None):
    db = SessionLocal()
    try:
        mv = db.get(MusicVideo, mv_id)
        result = relocate_video_file(db, mv, destination_rel=destination_rel)
        db.refresh(mv)
        result["db_file_path"] = mv.file_path
        return result
    finally:
        db.close()


# ----- 1. unknown 落点、无任何规则关联 → 无建议，自动移动拒绝 -----
mv_none = make_library_video("no-rule", "unknown/a.mp4")
unknown_dir = settings.library_dir / "unknown"
unknown_dir.mkdir(parents=True, exist_ok=True)
(unknown_dir / "a.mp4").write_bytes(b"video-a")
db = SessionLocal()
check(
    "无规则建议为空",
    suggest_relocate_path(db.get(MusicVideo, mv_none["id"])) is None,
)
db.close()
expect_error(
    "无规则时自动移动报错",
    lambda: relocate(mv_none["id"]),
    "手动指定目标路径",
)

# ----- 2. 规则3：官方 MV → 组合/歌曲/MV；伴随文件一并迁移 -----
mv_a = make_library_video(
    "mv-a", "unknown/b.mp4",
    group_name="Twice", song_name="CheerUp", video_types=["OfficialMV"],
)
unknown_dir = settings.library_dir / "unknown"
unknown_dir.mkdir(parents=True, exist_ok=True)
src_file = unknown_dir / "b.mp4"
src_file.write_bytes(b"video-b")
(unknown_dir / "b.mp4.info.json").write_text("{}")
(unknown_dir / "b.webp").write_bytes(b"cover")

db = SessionLocal()
sugg = suggest_relocate_path(db.get(MusicVideo, mv_a["id"]))
db.close()
check(
    "规则3建议路径",
    sugg is not None and sugg.as_posix() == "Twice/CheerUp/MV/b.mp4",
    sugg.as_posix() if sugg else "None",
)
r = relocate(mv_a["id"])
new_abs = settings.library_dir / "Twice" / "CheerUp" / "MV" / "b.mp4"
check("文件已移动到新目录", new_abs.is_file())
check("旧文件不存在", not src_file.exists())
check("DB 记录更新为新相对路径", r["db_file_path"] == "Twice/CheerUp/MV/b.mp4", r["db_file_path"])
check("返回 previous_path", r["previous_path"] == "unknown/b.mp4", r["previous_path"])
check(
    "伴随文件一并迁移（info.json + 封面）",
    sorted(r["moved_sidecars"]) == ["b.mp4.info.json", "b.webp"],
    str(r["moved_sidecars"]),
)
check(
    "伴随文件落在新目录",
    (settings.library_dir / "Twice" / "CheerUp" / "MV" / "b.webp").is_file()
    and (settings.library_dir / "Twice" / "CheerUp" / "MV" / "b.mp4.info.json").is_file(),
)
db = SessionLocal()
logs = (
    db.scalars(
        select(FileMoveLog).where(FileMoveLog.music_video_id == mv_a["id"])
    ).all()
)
db.close()
check(
    "FileMoveLog 已记录且状态 moved",
    len(logs) == 1 and logs[0].status == "moved",
    logs[0].status if logs else "无记录",
)

# ----- 3. 用户手动指定目标路径（覆盖规则） -----
mv_b = make_library_video("manual", "c.mp4")
(settings.library_dir / "c.mp4").write_bytes(b"video-c")
r2 = relocate(mv_b["id"], destination_rel="我的收藏/2024/c.mp4")
check(
    "手动路径移动成功",
    (settings.library_dir / "我的收藏" / "2024" / "c.mp4").is_file(),
    r2["db_file_path"],
)

# ----- 4. 目标同名冲突 → 自动加后缀 _1，不覆盖 -----
# mv_b 已在「我的收藏/2024/c.mp4」，用新视频 mv_d 移入被占用的同名目标触发冲突
(settings.library_dir / "我的收藏" / "2024" / "c.mp4").write_bytes(b"occupied")
mv_d = make_library_video("conflict", "d.mp4")
(settings.library_dir / "d.mp4").write_bytes(b"video-d")
r3 = relocate(mv_d["id"], destination_rel="我的收藏/2024/c.mp4")
check(
    "同名冲突加后缀 _1",
    r3["db_file_path"] == "我的收藏/2024/c_1.mp4",
    r3["db_file_path"],
)
check("原占用文件未被覆盖", (settings.library_dir / "我的收藏" / "2024" / "c.mp4").read_bytes() == b"occupied")

# ----- 5. 防护用例 -----
expect_error(
    "同路径 no-op 拒绝",
    lambda: relocate(mv_d["id"], destination_rel="我的收藏/2024/c_1.mp4"),
    "无需移动",
)
expect_error(
    "绝对路径拒绝",
    lambda: relocate(mv_d["id"], destination_rel=str(_tmp / "outside.mp4")),
    "相对路径",
)
expect_error(
    "越界路径拒绝",
    lambda: relocate(mv_d["id"], destination_rel="../../escape.mp4"),
    "超出正式库",
)

mv_incoming = make_library_video("incoming", "inc.mp4", **{"ingestion_status": "incoming"})
expect_error(
    "未入库视频拒绝移动",
    lambda: relocate(mv_incoming["id"]),
    "仅已入库",
)

missing = make_library_video("ghost", "ghost/x.mp4")
expect_error(
    "源文件缺失报 FileNotFoundError",
    lambda: relocate(missing["id"], destination_rel="y.mp4"),
    "源文件不存在",
)

# ----- 6. 大小写归并：已有 ive/ 目录时建议沿用现有大小写 -----
# 注意：不能用 Twice（测试2已建 Twice/CheerUp/MV，Windows 大小写不敏感，
# mkdir 无法再建出小写 twice/），换独立组合 IVE 验证。
mv_c = make_library_video(
    "case", "unknown/d.mp4",
    group_name="IVE", song_name="AfterLike", video_types=["OfficialMV"],
)
(settings.library_dir / "ive" / "afterlike").mkdir(parents=True, exist_ok=True)
db = SessionLocal()
sugg_c = suggest_relocate_path(db.get(MusicVideo, mv_c["id"]))
db.close()
check(
    "建议路径沿用已有目录大小写",
    sugg_c is not None and sugg_c.as_posix() == "ive/afterlike/MV/d.mp4",
    sugg_c.as_posix() if sugg_c else "None",
)


print(f"\n===== 结果: PASS={PASS} FAIL={FAIL} =====")
sys.exit(0 if FAIL == 0 else 1)
