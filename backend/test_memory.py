"""入库内存：扫描不哈希、info.json 流式提取、同盘移动不重哈希、转码并发含 CPU。"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="memory_fix_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.media.ffmpeg_engine import FfmpegEngineError  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.models.playback import TS_RUNNING, TS_STOPPED, TS_STOPPING, TranscodeSession  # noqa: E402
from app.services.file_service import compute_file_hash, drop_file_cache, move_to_library  # noqa: E402
from app.services.json_top_level import extract_json_top_level  # noqa: E402
from app.services.library_service import read_incoming_info, scan_incoming  # noqa: E402
from app.services.transcode_manager import transcode_manager  # noqa: E402
import app.services.file_service as file_service  # noqa: E402
import app.services.library_service as library_service  # noqa: E402

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


print("== 1. compute_file_hash + drop_file_cache ==")
blob = _tmp / "a.bin"
blob.write_bytes(b"hello-memory" * 1000)
expect = hashlib.sha256(blob.read_bytes()).hexdigest()
check("sha256 一致", compute_file_hash(blob) == expect)
drop_file_cache(blob)
check("drop_file_cache 不抛错", True)


print("== 2. info.json 流式提取，跳过巨型字段 ==")
info = _tmp / "incoming" / "clip.info.json"
info.parent.mkdir(parents=True, exist_ok=True)
huge = "x" * (2 * 1024 * 1024)
payload = {
    "title": "舞台直拍",
    "comments": huge,
    "automatic_captions": {"en": [{"ext": "vtt", "data": huge}]},
    "formats": [{"url": huge}],
    "thumbnail": "https://example.com/t.jpg",
    "view_count": 12,
    "description": "简介正文",
}
info.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
got = extract_json_top_level(
    info, ("title", "thumbnail", "view_count", "description", "comments")
)
check("取出 title", got.get("title") == "舞台直拍", str(got.get("title")))
check("取出 thumbnail（位于大字段之后）", got.get("thumbnail") == "https://example.com/t.jpg")
check("取出 view_count", got.get("view_count") == 12)
check("取出 description", got.get("description") == "简介正文")
check(
    "comments 超限被丢弃",
    "comments" not in got or got.get("comments") != huge,
)

video = _tmp / "incoming" / "clip.mp4"
video.write_bytes(b"fake-mp4")
read = read_incoming_info(video)
check("read_incoming_info 不含 comments", "comments" not in read)
check("read_incoming_info 有 title", read.get("title") == "舞台直拍")

small_desc = json.dumps({"title": "t", "description": "d" * 80})
capped = _tmp / "capped.info.json"
capped.write_text(small_desc, encoding="utf-8")
capped_got = extract_json_top_level(capped, ("title", "description"), max_value_bytes=40)
check("超限字段丢弃", "description" not in capped_got)
check("未超限字段保留", capped_got.get("title") == "t")

bom = _tmp / "bom.info.json"
bom.write_bytes(b"\xef\xbb\xbf" + json.dumps({"title": "bom"}).encode("utf-8"))
check("UTF-8 BOM", extract_json_top_level(bom, ("title",)).get("title") == "bom")


def _age(path: Path) -> None:
    past = time.time() - 120
    os.utime(path, (past, past))


print("== 3. 扫描不做全量哈希 ==")
incoming = settings.incoming_dir
incoming.mkdir(parents=True, exist_ok=True)
sample = incoming / "dup.mp4"
sample.write_bytes(b"12345")
_age(sample)
db = SessionLocal()
try:
    db.add(
        MusicVideo(
            name="already",
            file_name="dup.mp4",
            file_size=5,
            ingestion_status="library",
        )
    )
    db.commit()
finally:
    db.close()

orig_hash = file_service.compute_file_hash
orig_lib_hash = library_service.compute_file_hash

def boom(*_a, **_k):
    raise AssertionError("scan 不应计算全量哈希")

file_service.compute_file_hash = boom
library_service.compute_file_hash = boom
try:
    db = SessionLocal()
    try:
        items = scan_incoming(db)
    finally:
        db.close()
finally:
    file_service.compute_file_hash = orig_hash
    library_service.compute_file_hash = orig_lib_hash

by_name = {i.file_name: i for i in items}
check("扫描到 dup.mp4", "dup.mp4" in by_name, str(list(by_name)))
check("扫描 file_hash 为空", by_name.get("dup.mp4") and by_name["dup.mp4"].file_hash is None)
check("同名同大小标重复", by_name.get("dup.mp4") and by_name["dup.mp4"].is_duplicate is True)

other = incoming / "new.mp4"
other.write_bytes(b"12345")
_age(other)
db = SessionLocal()
try:
    items = scan_incoming(db)
finally:
    db.close()
by_name = {i.file_name: i for i in items}
check("不同文件名不误标重复", by_name.get("new.mp4") and by_name["new.mp4"].is_duplicate is False)


print("== 4. 同盘移动不重哈希目标文件 ==")
src = incoming / "move-me.mp4"
src.write_bytes(b"payload-for-move")
src_hash = compute_file_hash(src)
calls: list[str] = []
orig = file_service.compute_file_hash

def spy(path, algorithm="sha256"):
    calls.append(str(Path(path).resolve()))
    return orig(path, algorithm)

file_service.compute_file_hash = spy
try:
    db = SessionLocal()
    try:
        dest, log = move_to_library(
            db, src, Path("g") / "move-me.mp4", move=True, source_hash=src_hash
        )
        db.commit()
    finally:
        db.close()
finally:
    file_service.compute_file_hash = orig

check("同盘移动成功", dest.is_file() and log.status == "moved", log.status)
check("传入 source_hash 后不再读盘哈希", calls == [], str(calls))
check("hash_after 复用 hash_before", log.file_hash_after == src_hash)
check("verified", log.verified is True)

src2 = incoming / "copy-me.mp4"
src2.write_bytes(b"payload-for-copy")
calls.clear()
file_service.compute_file_hash = spy
try:
    db = SessionLocal()
    try:
        dest2, log2 = move_to_library(
            db, src2, Path("g") / "copy-me.mp4", move=False
        )
        db.commit()
    finally:
        db.close()
finally:
    file_service.compute_file_hash = orig
check("复制仍校验目标哈希", log2.status == "copied" and log2.verified)
check("复制至少哈希两次", len(calls) >= 2, str(calls))


print("== 5. 转码并发上限计入 CPU ==")
db = SessionLocal()
try:
    pid = os.getpid()
    db.add_all(
        [
            TranscodeSession(
                cache_key="cpu-1",
                profile_key="pk",
                source_path="/a.mp4",
                output_format="hls_mpegts",
                video_codec="libx264",
                audio_codec="aac",
                hardware_acceleration="cpu",
                status=TS_RUNNING,
                ffmpeg_pid=pid,
            ),
            TranscodeSession(
                cache_key="cpu-2",
                profile_key="pk",
                source_path="/b.mp4",
                output_format="hls_mpegts",
                video_codec="libx264",
                audio_codec="aac",
                hardware_acceleration="cpu",
                status=TS_RUNNING,
                ffmpeg_pid=pid,
            ),
            TranscodeSession(
                cache_key="remux-1",
                profile_key="pk",
                source_path="/c.mp4",
                output_format="hls_mpegts",
                video_codec="copy",
                audio_codec="copy",
                hardware_acceleration="none",
                status=TS_RUNNING,
                ffmpeg_pid=pid,
            ),
        ]
    )
    db.commit()
    raised = False
    try:
        transcode_manager._enforce_concurrency_limit(db, exclude_ts_id=-1)
    except FfmpegEngineError as e:
        raised = e.status_code in (429, 503)
        check("满额状态码 503/429", raised, str(e.status_code))
        check("满额文案含转码", "转码并发上限" in str(e), str(e))
    check("两路 CPU 转码触发上限", raised)

    db.query(TranscodeSession).delete()
    db.add_all(
        [
            TranscodeSession(
                cache_key="run-1",
                profile_key="pk",
                source_path="/e.mp4",
                output_format="hls_mpegts",
                video_codec="libx264",
                audio_codec="aac",
                hardware_acceleration="cpu",
                status=TS_RUNNING,
                ffmpeg_pid=pid,
            ),
            TranscodeSession(
                cache_key="stopping-1",
                profile_key="pk",
                source_path="/f.mp4",
                output_format="hls_mpegts",
                video_codec="libx264",
                audio_codec="aac",
                hardware_acceleration="cpu",
                status=TS_STOPPING,
                ffmpeg_pid=pid,
            ),
        ]
    )
    db.commit()
    transcode_manager._enforce_concurrency_limit(db, exclude_ts_id=-1)
    check("STOPPING 旧窗口不计入并发", True)

    class _FakeProc:
        def __init__(self):
            self.killed = False
            self.stopped = False
            self.pid = pid
            self.exit_code = -9

        @property
        def running(self):
            return not self.killed and not self.stopped

        def stop(self, timeout=None):
            self.stopped = True
            return -15

        def kill(self):
            self.killed = True
            return -9

    ts_force = TranscodeSession(
        cache_key="force-kill",
        profile_key="pk",
        source_path="/g.mp4",
        output_format="hls_mpegts",
        video_codec="libx264",
        audio_codec="aac",
        hardware_acceleration="cpu",
        status=TS_RUNNING,
        ffmpeg_pid=pid,
    )
    db.add(ts_force)
    db.commit()
    fake = _FakeProc()
    transcode_manager._processes[ts_force.id] = fake
    transcode_manager.stop_session(db, ts_force.id, force=True)
    check("force 停止走 kill 而不是优雅退出", fake.killed and not fake.stopped)
    db.refresh(ts_force)
    check("force 停止后状态 STOPPED", ts_force.status == TS_STOPPED, ts_force.status)

    db.query(TranscodeSession).delete()
    db.add(
        TranscodeSession(
            cache_key="remux-only",
            profile_key="pk",
            source_path="/d.mp4",
            output_format="hls_mpegts",
            video_codec="copy",
            audio_codec="copy",
            hardware_acceleration="none",
            status=TS_RUNNING,
            ffmpeg_pid=pid,
        )
    )
    db.commit()
    transcode_manager._enforce_concurrency_limit(db, exclude_ts_id=-1)
    check("remux 不计入并发", True)
finally:
    db.close()


print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
