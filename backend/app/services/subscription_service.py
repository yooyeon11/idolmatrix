"""收藏夹订阅：定期把 YouTube 播放列表里的新视频加入下载队列。

业主口径：订阅的是「用户某个 YouTube 收藏夹」。YouTube 的收藏夹本质就是
一个播放列表（`watch?v=…&list=PL…` 或 `playlist?list=PL…`），所以这里统一
归一化成 `playlist?list=<id>` 再交给 yt-dlp 展开。

首次订阅默认**只追新**（`download_existing=False`）：第一轮把列表里已有的
条目记为 `seen` 不入队，避免一订阅就灌几百条历史。想要历史可以在订阅上打开
「订阅时补下已有视频」。
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.download import DownloadJob, DownloadSubscription, DownloadSubscriptionItem
from app.services import duplicate_service, ytdlp_service
from app.services.ytdlp_service import YtdlpError

logger = logging.getLogger(__name__)

_LIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")


def normalize_playlist_url(url: str) -> str:
    """把任意 YouTube 链接归一化成播放列表地址；取不到 list 参数时原样返回。"""
    raw = (url or "").strip()
    if not raw:
        return raw
    try:
        parsed = urlparse(raw)
    except ValueError:
        return raw
    qs = parse_qs(parsed.query or "")
    list_id = (qs.get("list") or [""])[0].strip()
    if list_id and _LIST_ID_RE.match(list_id):
        return f"https://www.youtube.com/playlist?list={list_id}"
    return raw


def extract_list_id(url: str) -> Optional[str]:
    try:
        qs = parse_qs(urlparse(url).query or "")
    except ValueError:
        return None
    list_id = (qs.get("list") or [""])[0].strip()
    return list_id or None


def check_subscription(db, sub: DownloadSubscription) -> dict:
    """展开一次订阅列表，返回 {fetched, new, enqueued, skipped, error}。"""
    result = {"fetched": 0, "new": 0, "enqueued": 0, "skipped": 0, "in_library": 0, "error": ""}
    url = normalize_playlist_url(sub.url)
    try:
        data = ytdlp_service.probe(url, playlist=True)
    except (YtdlpError, Exception) as e:  # noqa: BLE001
        sub.last_checked_at = datetime.utcnow()
        sub.last_error = str(e)[:2000]
        db.commit()
        result["error"] = str(e)
        return result

    entries = data.get("entries") or []
    result["fetched"] = len(entries)
    title = (data.get("title") or "").strip()
    if title and not sub.name:
        sub.name = title[:300]

    # 已有台账（含本次新增）
    known = {
        row.video_id: row
        for row in db.scalars(
            select(DownloadSubscriptionItem).where(
                DownloadSubscriptionItem.subscription_id == sub.id
            )
        ).all()
    }
    first_run = not sub.initialized
    budget = max(1, int(settings.download_subscription_batch_limit))

    # 已在正式库 / 待整理 / 已下载过的视频：本轮直接跳过，避免重复占用空间
    # （业主口径：订阅也要先查重，不能因为是「新出现」就无条件下载）
    candidate_ids = [str(e.get("id") or "") for e in entries]
    already_have = duplicate_service.existing_video_ids(db, candidate_ids)
    result["in_library"] = 0

    for entry in entries:
        vid = str(entry.get("id") or "").strip()
        if not vid:
            continue
        if vid in known:
            result["skipped"] += 1
            continue

        entry_title = (entry.get("title") or "").strip()[:300] or None
        result["new"] += 1

        # 库里已经有了：记账为 in_library，不入队
        if vid in already_have:
            db.add(
                DownloadSubscriptionItem(
                    subscription_id=sub.id, video_id=vid, title=entry_title, state="in_library"
                )
            )
            known[vid] = True
            result["in_library"] += 1
            result["skipped"] += 1
            continue

        # 首次订阅且没勾「补下已有」→ 只记账不下载
        if first_run and not sub.download_existing:
            row = DownloadSubscriptionItem(
                subscription_id=sub.id, video_id=vid, title=entry_title, state="seen"
            )
            db.add(row)
            known[vid] = row
            continue

        if result["enqueued"] >= budget:
            result["skipped"] += 1
            continue

        # 已在队列/历史里（手动下过）→ 只记账
        existing_job = db.scalars(
            select(DownloadJob).where(DownloadJob.video_id == vid).limit(1)
        ).first()
        if existing_job:
            row = DownloadSubscriptionItem(
                subscription_id=sub.id,
                video_id=vid,
                title=entry_title,
                state="queued",
                job_id=existing_job.id,
            )
            db.add(row)
            known[vid] = row
            result["skipped"] += 1
            continue

        job = DownloadJob(
            url=f"https://www.youtube.com/watch?v={vid}",
            video_id=vid,
            mode="video",
            title=entry_title,
            status="queued",
            subscription_id=sub.id,
        )
        db.add(job)
        db.flush()
        db.add(
            DownloadSubscriptionItem(
                subscription_id=sub.id,
                video_id=vid,
                title=entry_title,
                state="queued",
                job_id=job.id,
            )
        )
        known[vid] = True
        result["enqueued"] += 1

    sub.initialized = True
    sub.last_checked_at = datetime.utcnow()
    sub.last_error = ""
    sub.total_enqueued = (sub.total_enqueued or 0) + result["enqueued"]
    db.commit()
    return result


def check_all(*, force_id: Optional[int] = None) -> list[dict]:
    """跑一轮检查（后台循环与手动「立即检查」共用）。"""
    db = SessionLocal()
    out: list[dict] = []
    try:
        stmt = select(DownloadSubscription).where(DownloadSubscription.enabled.is_(True))
        if force_id is not None:
            stmt = select(DownloadSubscription).where(DownloadSubscription.id == force_id)
        subs = db.scalars(stmt).all()
        for sub in subs:
            try:
                r = check_subscription(db, sub)
            except Exception as e:  # noqa: BLE001
                logger.exception("订阅 #%s 检查失败", sub.id)
                db.rollback()
                r = {"fetched": 0, "new": 0, "enqueued": 0, "skipped": 0, "in_library": 0, "error": str(e)}
            r["subscription_id"] = sub.id
            out.append(r)
            logger.info("订阅 #%s 检查完成：%s", sub.id, r)
    finally:
        db.close()
    return out


def subscription_loop(interval: float, stop: threading.Event) -> None:
    """后台周期检查。启动先等 60s，避免和启动扫描撞车。"""
    logger.info("YouTube 订阅检查任务已启动（间隔 %ss）", int(interval))
    if stop.wait(60):
        return
    while not stop.is_set():
        try:
            check_all()
        except Exception as e:  # noqa: BLE001
            logger.warning("订阅检查失败（不影响服务）：%s", e)
        if stop.wait(interval):
            break
    logger.info("YouTube 订阅检查任务已退出")
