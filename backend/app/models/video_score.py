"""VideoScore：AI 视频评分（实验功能）。

按视频类型感知的维度打分（清晰度/曝光色彩/遮挡/机位角度/构图），每维 1-10 分
+ 一句话理由。抽帧送多模态模型，结果按行存档（带模型名），换模型重跑保留历史。
实验模块：若功能下线，停用入口 + drop 本表即可，无其他耦合。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class VideoScore(TimestampMixin, Base):
    __tablename__ = "video_scores"
    __table_args__ = (
        Index("ix_video_scores_mv", "music_video_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    music_video_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("music_videos.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 评分模型名（追溯口径：同一视频换模型重跑保留各自历史）
    model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # 各维度评分：{"sharpness": {"score": 8, "reason": "..."}, ...}
    dims: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # 模型综合点评
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 送评帧数（成本/复现口径）
    frames: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scored_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    note: Mapped[Optional[str]] = mapped_column(String(500))
