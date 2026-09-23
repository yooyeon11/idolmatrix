"""观看记录：一次连续播放对应一行，供统计页观看排行使用。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class WatchPlay(TimestampMixin, Base):
    """一次连续观看（同一视频 10 分钟内的进度上报会合到同一行）。"""

    __tablename__ = "watch_plays"
    __table_args__ = (
        Index("ix_watch_plays_video_seen", "music_video_id", "last_seen_at"),
        Index("ix_watch_plays_started", "started_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    music_video_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("music_videos.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_position: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_position: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    seconds_watched: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source_duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
