"""incoming 扫描落库服务（方案 B 第一阶段）。

后台线程周期执行 sync_incoming_files：遍历 incoming 目录并与
incoming_files 表对账。

- 增量探测：mtime 与 file_size 均未变且上次探测成功 → 直接复用表内
  ffprobe 缓存，不再起子进程（落库的主要收益）
- 静置检查：mtime 距今小于 INCOMING_SETTLE_SECONDS 视为仍在写入，
  本轮跳过（保留旧行，不视为文件消失）
- 消失即删行：本轮未见到的路径从表里删除
- 挂载防护：目录不可访问或完全为空时不动表，避免 NAS 未挂载清空快照；
  真正清空（全部入库）由后续入库集成阶段删行
- is_duplicate 为扫描时按文件名+大小对 MusicVideo 的重复提示

注：第二阶段起 /library/scan 改读本表；POST /library/scan/sync 手动触发同步。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.media.ffprobe import ProbeError, probe
from app.models.incoming_file import IncomingFile
from app.models.music_video import MusicVideo
from app.services.file_service import drop_file_cache
from app.services.library_service import (
    INCOMING_SETTLE_SECONDS,
    VIDEO_EXTENSIONS,
    _dir_has_entries,
)

logger = logging.getLogger(__name__)

# 同一时间只允许一个扫描落库在跑（后台周期线程 / 后续的手动触发）
_scan_lock = threading.Lock()

# bili-sync 多P产物特征：Season 子目录 / S01E01 命名 / 同目录 tvshow.nfo
_MULTI_RE = re.compile(r"(?:^|[/\\])Season\s*\d+[/\\]", re.IGNORECASE)


def _is_bilibili_multip(path: Path) -> bool:
    """识别 bili-sync 多P分集（Season 目录或 SxxExx 命名），此类暂不支持入库。"""
    s = str(path)
    if _MULTI_RE.search(s):
        return True
    if re.search(r"[/\\]S\d{1,2}E\d{1,2}\.\w+$", s, re.IGNORECASE):
        return True
    # 同级 tvshow.nfo（多P整季结构标志）
    try:
        if (path.parent / "tvshow.nfo").is_file():
            return True
    except OSError:
        pass
    return False


def sync_incoming_files(db: Session) -> dict:
    """扫描 incoming 目录并与 incoming_files 表对账，返回统计。

    返回字段：scanned / added / updated / removed / skipped_settling /
    probe_failed / skipped_unmounted / skipped_running。
    """
    if not _scan_lock.acquire(blocking=False):
        return {"skipped_running": True}
    try:
        return _sync_locked(db)
    finally:
        _scan_lock.release()


def _sync_locked(db: Session) -> dict:
    stats: dict = {
        "scanned": 0,
        "added": 0,
        "updated": 0,
        "removed": 0,
        "skipped_settling": 0,
        "probe_failed": 0,
        "skipped_unmounted": False,
        "skipped_multip": 0,
    }

    candidates: list[tuple[Path, float, int]] = []
    any_mounted = False
    for root_dir in settings.incoming_scan_dirs:
        if not _dir_has_entries(root_dir):
            continue
        any_mounted = True
        try:
            for path in root_dir.rglob("*"):
                try:
                    if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
                        continue
                    st = path.stat()
                except OSError:
                    continue  # 文件刚被移走/删除
                candidates.append((path, st.st_mtime, st.st_size))
        except OSError as e:
            # 遍历中途失败（疑似掉挂载），该目录本轮不计
            logger.warning("incoming 目录遍历失败，本轮跳过落库：%s（%s）", root_dir, e)
    if not any_mounted:
        stats["skipped_unmounted"] = True
        return stats

    # 多P目录跳过：bili-sync 多P产物为 {video_name}/Season 1/S01E01.mp4（含 tvshow.nfo）
    filtered: list[tuple[Path, float, int]] = []
    for path, mtime, size in candidates:
        if _is_bilibili_multip(path):
            stats["skipped_multip"] += 1
            logger.info("跳过 B站多P分集（暂不支持）：%s", path)
            continue
        filtered.append((path, mtime, size))
    candidates = filtered

    now = time.time()
    rows = {r.file_path: r for r in db.scalars(select(IncomingFile)).all()}
    seen: set[str] = set()

    for path, mtime, size in candidates:
        stats["scanned"] += 1
        key = str(path)
        # 先记入 seen 再做静置跳过：文件仍在（只是写入中），旧行不能当消失删掉
        seen.add(key)
        if now - mtime < INCOMING_SETTLE_SECONDS:
            stats["skipped_settling"] += 1
            continue

        row = rows.get(key)
        if (
            row is not None
            and row.mtime == mtime
            and row.file_size == size
            and row.probed_at is not None
        ):
            continue  # 未变化且探测成功过 → 复用缓存

        duration = width = height = None
        video_codec = audio_codec = None
        probed_at = None
        try:
            result = probe(path)
            duration = result.duration_seconds_int
            width = result.width
            height = result.height
            video_codec = result.video_codec
            audio_codec = result.audio_codec
            probed_at = datetime.utcnow()
        except ProbeError as e:
            logger.warning("探测失败 %s: %s", path, e)
            stats["probe_failed"] += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("探测异常 %s: %s", path, e)
            stats["probe_failed"] += 1
        finally:
            drop_file_cache(path)

        # 扫描阶段不做全量 SHA256（与 _scan_one 同口径）：文件名+大小做重复提示
        is_duplicate = (
            db.scalar(
                select(MusicVideo.id).where(
                    MusicVideo.deleted_at.is_(None),
                    MusicVideo.file_name == path.name,
                    MusicVideo.file_size == size,
                ).limit(1)
            )
            is not None
        )

        if row is None:
            db.add(
                IncomingFile(
                    file_path=key,
                    file_name=path.name,
                    file_size=size,
                    mtime=mtime,
                    duration=duration,
                    width=width,
                    height=height,
                    video_codec=video_codec,
                    audio_codec=audio_codec,
                    is_duplicate=is_duplicate,
                    probed_at=probed_at,
                )
            )
            stats["added"] += 1
        else:
            row.file_name = path.name
            row.file_size = size
            row.mtime = mtime
            row.duration = duration
            row.width = width
            row.height = height
            row.video_codec = video_codec
            row.audio_codec = audio_codec
            row.is_duplicate = is_duplicate
            row.probed_at = probed_at
            stats["updated"] += 1

    gone = [r for r in rows.values() if r.file_path not in seen]
    if gone:
        db.execute(
            delete(IncomingFile).where(IncomingFile.id.in_([r.id for r in gone]))
        )
        stats["removed"] = len(gone)

    db.commit()
    return stats
