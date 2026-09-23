"""照片宫格缩略图：写在 derived/photo-thumbs/{id}.jpg，不改动原文件。

扫描只建索引；缩略图由后台队列慢慢补，GET /photos/{id}/thumb 时若缺失则现做一张。
"""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.ffmpeg import FFmpegNotFoundError
from app.media.ffmpeg import FFmpegError, extract_photo_thumbnail
from app.models.photo import Photo

logger = logging.getLogger(__name__)

THUMB_WIDTH = 480
_FFMPEG_SLOTS = threading.Semaphore(3)
_queue: queue.Queue[int] = queue.Queue()
_queued: set[int] = set()
_queued_lock = threading.Lock()
_id_locks_guard = threading.Lock()
_id_locks: dict[int, threading.Lock] = {}
_worker_started = False
_worker_guard = threading.Lock()
_backfilled_sources: set[int] = set()
_backfill_guard = threading.Lock()


def thumb_abs(photo_id: int) -> Path:
    return settings.photo_thumb_dir / f"{int(photo_id)}.jpg"


def remove_thumb(photo_id: int) -> None:
    dest = thumb_abs(photo_id)
    try:
        dest.unlink(missing_ok=True)
        dest.with_name(dest.stem + ".part.jpg").unlink(missing_ok=True)
    except OSError:
        pass


def remove_thumbs(photo_ids: Iterable[int]) -> None:
    for pid in photo_ids:
        remove_thumb(pid)


def _lock_for(photo_id: int) -> threading.Lock:
    with _id_locks_guard:
        lock = _id_locks.get(photo_id)
        if lock is None:
            lock = threading.Lock()
            _id_locks[photo_id] = lock
        return lock


def _thumb_is_fresh(src: Path, dest: Path) -> bool:
    try:
        if not dest.is_file() or dest.stat().st_size <= 0:
            return False
        if src.is_file() and src.stat().st_mtime > dest.stat().st_mtime + 1:
            return False
    except OSError:
        return False
    return True


def generate_thumbnail(photo: Photo) -> Path:
    """根据索引记录生成或复用缩略图，路径始终在 derived/photo-thumbs 内。"""
    from app.services import photo_service

    dest = thumb_abs(photo.id)
    if photo_service.is_mtphotos_photo(photo):
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        with _lock_for(photo.id):
            if dest.is_file() and dest.stat().st_size > 0:
                return dest
            db = SessionLocal()
            try:
                data = photo_service.fetch_mtphotos_thumb(db, photo)
            finally:
                db.close()
            dest.parent.mkdir(parents=True, exist_ok=True)
            part = dest.with_name(dest.stem + ".part.jpg")
            part.write_bytes(data)
            part.replace(dest)
        return dest
    src = photo_service.resolve_photo_file(photo)
    if _thumb_is_fresh(src, dest):
        return dest
    with _lock_for(photo.id):
        if _thumb_is_fresh(src, dest):
            return dest
        with _FFMPEG_SLOTS:
            extract_photo_thumbnail(
                src,
                dest,
                is_video=photo.media_kind == "video",
                width=THUMB_WIDTH,
            )
    return dest


def resolve_or_build_thumb(photo: Photo) -> Path:
    try:
        return generate_thumbnail(photo)
    except (FFmpegError, FFmpegNotFoundError, OSError) as e:
        logger.warning("照片缩略图失败 id=%s: %s", photo.id, e)
        raise


def enqueue_photo_thumbs(photo_ids: Iterable[int]) -> int:
    """把需要补缩略图的 id 丢进后台队列。已在队或已有新缩略图的跳过。"""
    added = 0
    pending: list[int] = []
    with _queued_lock:
        for raw in photo_ids:
            pid = int(raw)
            if pid <= 0 or pid in _queued:
                continue
            _queued.add(pid)
            pending.append(pid)
    for pid in pending:
        _queue.put(pid)
        added += 1
    if added:
        _ensure_worker()
    return added


def enqueue_missing_for_source(source_id: int) -> int:
    db = SessionLocal()
    try:
        ids = list(
            db.scalars(
                select(Photo.id).where(
                    Photo.source_id == source_id,
                    Photo.active_filter(),
                )
            ).all()
        )
    finally:
        db.close()
    return enqueue_photo_thumbs(ids)


def maybe_backfill_source(source_id: int) -> None:
    """每个来源进程内只入队一次，给已扫描但还没缩略图的旧索引补齐。"""
    with _backfill_guard:
        if source_id in _backfilled_sources:
            return
        _backfilled_sources.add(source_id)
    enqueue_missing_for_source(source_id)


def _ensure_worker() -> None:
    global _worker_started
    with _worker_guard:
        if _worker_started:
            return
        t = threading.Thread(target=_worker_loop, name="photo-thumbs", daemon=True)
        t.start()
        _worker_started = True
        logger.info("照片缩略图后台线程已启动")


def _worker_loop() -> None:
    while True:
        pid = _queue.get()
        try:
            _build_queued(pid)
        except Exception as e:  # noqa: BLE001
            logger.warning("照片缩略图后台失败 id=%s: %s", pid, e)
        finally:
            with _queued_lock:
                _queued.discard(pid)
            _queue.task_done()


def _build_queued(photo_id: int) -> None:
    db = SessionLocal()
    try:
        photo = db.scalar(
            select(Photo)
            .options(selectinload(Photo.source))
            .where(Photo.id == photo_id, Photo.active_filter())
        )
        if not photo:
            remove_thumb(photo_id)
            return
        generate_thumbnail(photo)
    finally:
        db.close()
