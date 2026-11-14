"""YouTube 下载任务与订阅模型。

- DownloadJob：单次下载任务（单视频 / 整个播放列表），排队 → 下载 → 合并 → 移入 incoming。
- DownloadSubscription：订阅某个 YouTube 收藏夹（播放列表），定期检查新视频并入队。

设计要点（业主 2026-10-18 口径）：
1. 产物落 `storage_incoming_dir`（NAS = /data/incoming）；
2. 命名模板沿用库内既有口径 `%(upload_date>%y-%m-%d)s %(title)s[%(uploader)s] (%(id)s)`；
3. 画质 `bestvideo+bestaudio`；
4. **下载完整才进待整理**：先写 `incoming/.downloading/<job_id>/`，成功后整体移动到
   incoming 根，扫描器同时忽略隐藏目录，双重保证半成品不会被扫描到。
5. yt-dlp 二进制放在可写卷（默认 /data/tools），容器内可直接自更新，无需重建镜像。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin

# 任务状态机：queued → downloading → processing(合并/移动) → done
#                                          ↘ failed / canceled
DOWNLOAD_STATUS = ("queued", "downloading", "processing", "done", "failed", "canceled")
ACTIVE_STATUS = ("queued", "downloading", "processing")


class DownloadJob(TimestampMixin, Base):
    """一个下载任务。"""

    __tablename__ = "download_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 原始输入（可能是视频链接，也可能是播放列表链接）
    url: Mapped[str] = mapped_column(String(1000))
    # 解析出的视频 ID（YouTube 11 位 ID）；播放列表任务为 NULL
    video_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    # video = 单视频；playlist = 整个列表
    mode: Mapped[str] = mapped_column(String(16), default="video")
    title: Mapped[Optional[str]] = mapped_column(String(300))
    uploader: Mapped[Optional[str]] = mapped_column(String(200))
    duration: Mapped[Optional[int]] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    # 0~100（多文件任务取加权平均）
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    downloaded_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    total_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    speed: Mapped[Optional[str]] = mapped_column(String(32))
    eta: Mapped[Optional[str]] = mapped_column(String(16))
    # 失败/取消原因（原文，便于排查 yt-dlp 的具体报错）
    error: Mapped[Optional[str]] = mapped_column(Text)
    # 成功后落盘文件（相对 incoming 根的路径，分号分隔；播放列表可能多个）
    output_path: Mapped[Optional[str]] = mapped_column(String(2000))
    # 落盘明细 JSON：[{kind, name, size, width, height}]
    outputs: Mapped[Optional[str]] = mapped_column(Text)
    # 本次是否下载字幕
    download_subtitles: Mapped[bool] = mapped_column(Boolean, default=False)

    subscription_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("download_subscriptions.id", ondelete="SET NULL"), index=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class DownloadSubscription(TimestampMixin, Base):
    """订阅一个 YouTube 收藏夹 / 播放列表，定期抓新视频。"""

    __tablename__ = "download_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(1000))
    # 展示名（首次检查时自动填列表标题）
    name: Mapped[Optional[str]] = mapped_column(String(300))
    enabled: Mapped[bool] = mapped_column(default=True)
    # 首次订阅时是否把「列表里已有的视频」全部下载（默认否：只追新）
    download_existing: Mapped[bool] = mapped_column(default=False)
    # 首次扫描是否已完成（未完成时按 download_existing 决定要不要入队）
    initialized: Mapped[bool] = mapped_column(default=False)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    # 累计已入队条数（展示用）
    total_enqueued: Mapped[int] = mapped_column(Integer, default=0)


class DownloadSubscriptionItem(TimestampMixin, Base):
    """订阅里出现过的视频（去重台账）。

    订阅每轮都会把列表展开一遍，这里记住「这条已经处理过」，
    避免同一个视频被重复入队/重复下载；也用于 UI 展示订阅历史。
    """

    __tablename__ = "download_subscription_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("download_subscriptions.id", ondelete="CASCADE"), index=True
    )
    video_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[Optional[str]] = mapped_column(String(300))
    # seen = 首次订阅时已存在（不下载）/ queued = 已入队 / done = 已下载完成
    state: Mapped[str] = mapped_column(String(16), default="seen")
    job_id: Mapped[Optional[int]] = mapped_column(Integer)

