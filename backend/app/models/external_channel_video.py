"""ExternalChannelVideo：博主频道完整投稿快照（YouTube）。

设计要点：
1. 以 uploader_key（博主名归一化键：NFKC + 去空白/不可见字符 + casefold）关联博主规则，
   与固定视频类型规则同键；旧版写入的 casefold 键读取时一并归一化兼容。
2. 存快照而非只存 diff：既支撑「缺失 = 频道全集 − 库内 source_id 集合」比对，
   也能顺带感知频道侧已删除的视频；last_seen_at 记录最近一次检测的可见性。
3. video_id 即 YouTube 视频 ID，与 MusicVideo.source_id 同口径比对。
4. 封面检测时下载到 derived/yt-covers/ 本地缓存，thumbnail_path 存相对路径，
   前端不直连 i.ytimg.com。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampMixin
from app.core.database import Base


class ExternalChannelVideo(TimestampMixin, Base):
    __tablename__ = "external_channel_videos"
    __table_args__ = (
        # 一个博主名下每个外部视频只留一行快照
        UniqueConstraint("uploader_key", "video_id", name="uq_external_channel_video"),
        Index("ix_external_channel_videos_uploader", "uploader_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 博主名归一化键（app_settings.uploader_key），与 app_settings 博主规则同键
    uploader_key: Mapped[str] = mapped_column(String(200), nullable=False)
    # YouTube 视频 ID（与 MusicVideo.source_id 同口径）
    video_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), default="")
    # 相对 derived/yt-covers 的封面缓存文件名；下载失败为 NULL
    thumbnail_path: Mapped[Optional[str]] = mapped_column(String(500))
    # 秒（YouTube contentDetails.duration 换算）
    duration: Mapped[Optional[int]] = mapped_column(Integer)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # 复用库内短视频口径：时长 < 70 秒视为 Shorts
    is_short: Mapped[bool] = mapped_column(Boolean, default=False)
    # 最近一次检测仍存在于频道投稿列表的时间；被删出快照的行直接物理删除
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
