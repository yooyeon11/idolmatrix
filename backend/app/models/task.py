"""入库文件移动日志模型。

- FileMoveLog：入库 / 整理 / 归档时的文件移动日志，含校验结果。

注：旧版 TranscodeTask 已随 Phase 8 重构移除，实时播放转码改由
TranscodeSession（models/playback.py）承载。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FileMoveLog(Base):
    """文件移动日志：入库 / 整理 / 归档时使用。"""

    __tablename__ = "file_move_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    music_video_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("music_videos.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_path: Mapped[str] = mapped_column(String(1000))
    destination_path: Mapped[str] = mapped_column(String(1000))
    # moved / copied / failed / skipped
    status: Mapped[str] = mapped_column(String(20))
    file_hash_before: Mapped[Optional[str]] = mapped_column(String(64))
    file_hash_after: Mapped[Optional[str]] = mapped_column(String(64))
    verified: Mapped[bool] = mapped_column(default=False)
    message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
