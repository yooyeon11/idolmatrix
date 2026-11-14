"""VideoPublish：视频在外部平台（当前只有 B 站）的分发记录。

设计要点（P0，业主 2026-11-08）：

1. **主路径是「复用别人已投的稿」，不是「自己上传」。**
   同一个舞台在 B 站大概率已经有搬运，直接把 `bvid` 挂上来零成本、
   零风控、零画质损失。`origin` 用来区分这两种来源，只有
   `owned_by_me=True` 的记录才参与「投稿 / 撤稿」管理。

2. **status 是播放可用性的唯一判据。**
   B 站稿件会进审核、被版权锁定、被 UP 主删除。任何非 `ok` 的状态
   前端都必须显示「暂不可在线观看」，**绝不能让 iframe 指向这些稿件**
   ——否则用户看到的是「视频不存在」，比明确的占位更糟。

3. **本表不影响本地播放。**
   本地有文件时永远优先本地（Direct Play / Transcode），
   只有文件缺失或处于 mirror 只读模式时才走外部源。

4. 一条 MusicVideo 在同一平台最多一条有效记录（软删除豁免）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import BaseEntityMixin

# ===== 播放可用状态 =====
PUBLISH_PENDING = "pending"      # 已排入待发布/待匹配，尚无 bvid
PUBLISH_REVIEWING = "reviewing"  # 已投稿，审核中（B 站审核期间不对外开放）
PUBLISH_OK = "ok"                # 可正常外链播放
PUBLISH_LOCKED = "locked"        # 被锁定（版权/违规），仅 UP 主自己可见
PUBLISH_MISSING = "missing"      # 稿件已删除或 BV 号失效

PUBLISH_STATUSES = frozenset(
    {PUBLISH_PENDING, PUBLISH_REVIEWING, PUBLISH_OK, PUBLISH_LOCKED, PUBLISH_MISSING}
)

# ===== 来源 =====
ORIGIN_SELF_UPLOAD = "self_upload"   # 自己投稿
ORIGIN_THIRD_PARTY = "third_party"   # 复用 B 站上他人已有的同一内容
ORIGIN_MANUAL = "manual"             # 人工粘贴 BV 号

PUBLISH_ORIGINS = frozenset({ORIGIN_SELF_UPLOAD, ORIGIN_THIRD_PARTY, ORIGIN_MANUAL})


class VideoPublish(BaseEntityMixin, Base):
    __tablename__ = "video_publishes"
    __table_args__ = (
        # 一个视频在同一平台只保留一条有效记录
        Index(
            "uq_video_publishes_active",
            "music_video_id",
            "platform",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index("ix_video_publishes_active", "status", "deleted_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    music_video_id: Mapped[int] = mapped_column(
        ForeignKey("music_videos.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # 平台标识；当前只有 bilibili，留字段避免以后加 YouTube 时改表
    platform: Mapped[str] = mapped_column(String(20), default="bilibili", nullable=False)

    # BV 号（不含 https:// 前缀）；pending 状态允许为空
    bvid: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    # 分 P 索引（B 站分 P 投稿：一个 BV 号承载多个视频）；从 1 开始
    page: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    origin: Mapped[str] = mapped_column(String(20), default=ORIGIN_MANUAL, nullable=False)
    # True = 这是我自己账号投的稿，可以管理（改标题/撤稿）；False = 别人的
    owned_by_me: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(20), default=PUBLISH_PENDING, index=True, nullable=False
    )

    # ===== 自动匹配证据（P1 用，P0 可空）=====
    # 0~1，标题相似度 + 时长一致性的综合分；人工绑定为 NULL
    match_score: Mapped[Optional[float]] = mapped_column(Float)
    # 人类可读的匹配依据，例如「时长一致(178s) · 标题相似 0.92」
    match_note: Mapped[Optional[str]] = mapped_column(String(300))
    # 原出处（third_party 时填 B 站视频地址；self_upload 时填原始来源）
    source_url: Mapped[Optional[str]] = mapped_column(String(500))

    # ===== 生命周期 =====
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # 最近一次确认稿件仍可播放的时间
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # 失败原因（投稿报错 / 状态检查报错），供详情页展示
    error: Mapped[Optional[str]] = mapped_column(Text)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<VideoPublish mv={self.music_video_id} {self.bvid} [{self.status}]>"
