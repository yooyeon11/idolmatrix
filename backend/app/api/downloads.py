"""YouTube 下载与收藏夹订阅接口。

- 任务：创建 / 列表 / 取消 / 重试 / 删除记录
- 订阅：创建 / 列表 / 开关 / 立即检查 / 删除
- yt-dlp：版本查询 / 自更新（容器内升级，无需重建镜像）
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import delete, select

from app.core.deps import DbDep
from app.models.download import DownloadJob, DownloadSubscription, DownloadSubscriptionItem
from app.schemas.download import (
    DownloadCheckRequest,
    DownloadCheckResult,
    DownloadJobCreate,
    DownloadJobRead,
    DownloadSubscriptionCreate,
    DownloadSubscriptionRead,
    DownloadSubscriptionUpdate,
    NetworkProbeResult,
    SubscriptionCheckResult,
    YtdlpStatus,
    YtdlpUpdateRequest,
)
from app.services import download_service, duplicate_service, subscription_service, ytdlp_service

router = APIRouter(prefix="/downloads", tags=["downloads"])


def _job_read(job: DownloadJob) -> DownloadJobRead:
    return DownloadJobRead.model_validate(job)


# ---------------- 任务 ----------------
@router.get("/jobs", response_model=list[DownloadJobRead])
def list_jobs(
    db: DbDep,
    status: Optional[str] = Query(default=None, description="按状态过滤，逗号分隔"),
    limit: int = Query(default=60, ge=1, le=300),
):
    stmt = select(DownloadJob).order_by(DownloadJob.id.desc()).limit(limit)
    if status:
        wanted = [s.strip() for s in status.split(",") if s.strip()]
        if wanted:
            stmt = (
                select(DownloadJob)
                .where(DownloadJob.status.in_(wanted))
                .order_by(DownloadJob.id.desc())
                .limit(limit)
            )
    return [_job_read(j) for j in db.scalars(stmt).all()]


@router.post("/check", response_model=DownloadCheckResult)
def check_before_download(payload: DownloadCheckRequest, db: DbDep):
    """下载前预检：解析链接 + 查重（不建任务）。

    前端先调这个接口；命中重复就弹确认框，用户点「仍然下载」才带 force 建任务。
    """
    url = payload.url.strip()
    mode = "playlist" if payload.mode == "playlist" else "video"
    if not ytdlp_service.version().get("available"):
        raise HTTPException(400, "yt-dlp 不可用，请先在设置里点「更新 yt-dlp」")

    if mode == "playlist":
        return DownloadCheckResult(url=url, duplicate=False, message="")

    try:
        info = ytdlp_service.probe(url, playlist=False)
    except ytdlp_service.YtdlpError as e:
        raise HTTPException(400, f"解析链接失败：{e}") from e

    video_id = str(info.get("id") or "")[:64] or None
    dur = info.get("duration")
    duration = int(dur) if isinstance(dur, (int, float)) else None
    dup = duplicate_service.check_video_duplicate(
        db, video_id=video_id, title=info.get("title"), duration=duration
    )
    return DownloadCheckResult(
        url=url,
        video_id=video_id,
        title=(info.get("title") or "")[:300] or None,
        uploader=(info.get("uploader") or "")[:200] or None,
        duration=duration,
        duplicate=dup["duplicate"],
        message=dup["message"],
        matches=dup["matches"],
    )


@router.post("/jobs", response_model=DownloadJobRead, status_code=201)
def create_job(payload: DownloadJobCreate, db: DbDep):
    url = payload.url.strip()
    mode = "playlist" if payload.mode == "playlist" else "video"
    if not ytdlp_service.version().get("available"):
        raise HTTPException(400, "yt-dlp 不可用，请先在设置里点「更新 yt-dlp」")

    video_id: Optional[str] = None
    title: Optional[str] = None
    uploader: Optional[str] = None
    duration: Optional[int] = None
    try:
        if mode == "playlist":
            url = subscription_service.normalize_playlist_url(url)
            info = ytdlp_service.probe(url, playlist=True)
            title = (info.get("title") or "")[:300] or None
        else:
            info = ytdlp_service.probe(url, playlist=False)
            video_id = str(info.get("id") or "")[:64] or None
            title = (info.get("title") or "")[:300] or None
            uploader = (info.get("uploader") or "")[:200] or None
            dur = info.get("duration")
            if isinstance(dur, (int, float)):
                duration = int(dur)
    except ytdlp_service.YtdlpError as e:
        raise HTTPException(400, f"解析链接失败：{e}") from e

    # 查重：正式库 / 待整理 / 队列里已有同一条 → 409，让前端提示（除非 force）
    if not payload.force:
        dup = duplicate_service.check_video_duplicate(db, video_id=video_id, duration=duration)
        if dup["duplicate"]:
            raise HTTPException(409, dup["message"])

    # 同一条视频已在队列/已完成 → 直接返回那一条，避免重复下载
    if video_id:
        existing = db.scalars(
            select(DownloadJob)
            .where(DownloadJob.video_id == video_id)
            .where(DownloadJob.status.in_(("queued", "downloading", "processing", "done")))
            .order_by(DownloadJob.id.desc())
            .limit(1)
        ).first()
        if existing:
            return _job_read(existing)

    job = DownloadJob(
        url=url,
        video_id=video_id,
        mode=mode,
        title=title,
        uploader=uploader,
        duration=duration,
        status="queued",
        download_subtitles=bool(payload.download_subtitles),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_read(job)


@router.post("/jobs/{job_id}/subtitles")
def fetch_job_subtitles(job_id: int, db: DbDep):
    """给已完成的下载补下字幕（后台执行，前端刷新列表看结果）。"""
    import threading

    job = db.get(DownloadJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    if job.status != "done":
        raise HTTPException(400, "只有已完成的下载可以补字幕")

    def _run() -> None:
        try:
            download_service.fetch_subtitles(job_id)
        except Exception as e:  # noqa: BLE001 — 后台任务失败只记日志
            import logging

            logging.getLogger(__name__).warning("补字幕失败（job %s）：%s", job_id, e)

    threading.Thread(target=_run, daemon=True, name=f"subs-{job_id}").start()
    return {"started": True}


@router.post("/jobs/{job_id}/cancel", response_model=DownloadJobRead)
def cancel_job(job_id: int, db: DbDep):
    job = db.get(DownloadJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    if job.status in ("queued", "downloading", "processing"):
        download_service.worker.request_cancel(job_id)
        if job.status == "queued":
            job.status = "canceled"
            job.finished_at = datetime.utcnow()
            job.error = "已取消"
    db.commit()
    db.refresh(job)
    return _job_read(job)


@router.post("/jobs/{job_id}/retry", response_model=DownloadJobRead)
def retry_job(job_id: int, db: DbDep):
    job = db.get(DownloadJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    if job.status in ("queued", "downloading", "processing"):
        raise HTTPException(400, "任务仍在进行中")
    job.status = "queued"
    job.progress = 0.0
    job.error = None
    job.speed = None
    job.eta = None
    job.finished_at = None
    job.started_at = None
    db.commit()
    db.refresh(job)
    return _job_read(job)


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: int, db: DbDep):
    job = db.get(DownloadJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    if job.status in ("downloading", "processing"):
        download_service.worker.request_cancel(job_id)
    db.execute(delete(DownloadJob).where(DownloadJob.id == job_id))
    db.commit()


# ---------------- 订阅 ----------------
@router.get("/subscriptions", response_model=list[DownloadSubscriptionRead])
def list_subscriptions(db: DbDep):
    rows = db.scalars(select(DownloadSubscription).order_by(DownloadSubscription.id.desc())).all()
    return [DownloadSubscriptionRead.model_validate(r) for r in rows]


@router.post("/subscriptions", response_model=DownloadSubscriptionRead, status_code=201)
def create_subscription(payload: DownloadSubscriptionCreate, db: DbDep):
    url = subscription_service.normalize_playlist_url(payload.url.strip())
    list_id = subscription_service.extract_list_id(url)
    if not list_id:
        raise HTTPException(400, "请粘贴 YouTube 收藏夹 / 播放列表链接（地址里要带 list=…）")
    exists = db.scalars(
        select(DownloadSubscription).where(DownloadSubscription.url == url).limit(1)
    ).first()
    if exists:
        raise HTTPException(400, "这个列表已经订阅过了")
    sub = DownloadSubscription(
        url=url,
        name=(payload.name or "").strip()[:300] or None,
        download_existing=bool(payload.download_existing),
        enabled=True,
        initialized=False,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return DownloadSubscriptionRead.model_validate(sub)


@router.patch("/subscriptions/{sub_id}", response_model=DownloadSubscriptionRead)
def update_subscription(sub_id: int, payload: DownloadSubscriptionUpdate, db: DbDep):
    sub = db.get(DownloadSubscription, sub_id)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    if payload.enabled is not None:
        sub.enabled = payload.enabled
    if payload.name is not None:
        sub.name = payload.name.strip()[:300] or None
    if payload.download_existing is not None:
        sub.download_existing = payload.download_existing
    db.commit()
    db.refresh(sub)
    return DownloadSubscriptionRead.model_validate(sub)


@router.post("/subscriptions/{sub_id}/check", response_model=SubscriptionCheckResult)
def check_subscription_now(sub_id: int, db: DbDep):
    sub = db.get(DownloadSubscription, sub_id)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    r = subscription_service.check_subscription(db, sub)
    r["subscription_id"] = sub_id
    return SubscriptionCheckResult(**r)


@router.delete("/subscriptions/{sub_id}", status_code=204)
def delete_subscription(sub_id: int, db: DbDep):
    sub = db.get(DownloadSubscription, sub_id)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    db.execute(
        delete(DownloadSubscriptionItem).where(DownloadSubscriptionItem.subscription_id == sub_id)
    )
    db.delete(sub)
    db.commit()


# ---------------- yt-dlp ----------------
@router.get("/ytdlp", response_model=YtdlpStatus)
def ytdlp_status():
    from app.services import archive_service

    return YtdlpStatus(
        **ytdlp_service.version(),
        archive_path=str(archive_service.archive_path()),
        archive_count=archive_service.count(),
    )


@router.post("/ytdlp/test-network", response_model=NetworkProbeResult)
def ytdlp_test_network(payload: YtdlpUpdateRequest | None = None):
    """逐个下载源自检连通性：能看出「有没有走代理」「哪个源能用」。"""
    return NetworkProbeResult(**ytdlp_service.test_network(payload.channel if payload else None))


@router.post("/ytdlp/update", response_model=YtdlpStatus)
def ytdlp_update(payload: YtdlpUpdateRequest | None = None):
    try:
        info = ytdlp_service.update(payload.channel if payload else None)
    except ytdlp_service.YtdlpError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"更新失败：{e}") from e
    return YtdlpStatus(**info)
