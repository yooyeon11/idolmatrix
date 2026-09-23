"""Playback 会话与转码会话模型（Emby 式播放架构核心）。

架构目标（对标 Emby / Plex 的播放链路）：

- PlaybackSession：一次「播放器会话」。记录播放决策结果
  （Direct Play / Direct Stream / Transcode）与播放状态机。
  播放器只面对它，不直接接触 FFmpeg。
- TranscodeSession：一次「实际转码会话」，对应一个长期运行的 FFmpeg
  进程。多个 PlaybackSession 可以复用同一个 TranscodeSession。

去重规则：
- TranscodeSession.cache_key 唯一（相同源文件 + 相同转码 profile +
  相同流协议），保证同一时刻同一规格只存在一个 FFmpeg 进程，
  多个播放器共享它的 HLS/MPEG-TS 输出。

状态机：
- PlaybackSession：created → starting → playing / paused → stopping → stopped
  （terminal：stopped / completed / failed）
- TranscodeSession：created → starting → running → stopping → stopped
  （terminal：stopped / completed / failed）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import (
    JSON,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import BaseEntityMixin

# ===== PlaybackSession 状态机 =====
PS_CREATED = "created"
PS_STARTING = "starting"
PS_PLAYING = "playing"
PS_PAUSED = "paused"
PS_STOPPING = "stopping"
PS_STOPPED = "stopped"
PS_COMPLETED = "completed"
PS_FAILED = "failed"

PLAYBACK_STATUSES = frozenset(
    {
        PS_CREATED,
        PS_STARTING,
        PS_PLAYING,
        PS_PAUSED,
        PS_STOPPING,
        PS_STOPPED,
        PS_COMPLETED,
        PS_FAILED,
    }
)

# ===== TranscodeSession 状态机 =====
TS_CREATED = "created"
TS_STARTING = "starting"
TS_RUNNING = "running"
TS_STOPPING = "stopping"
TS_STOPPED = "stopped"
TS_COMPLETED = "completed"
TS_FAILED = "failed"

TRANSCODE_STATUSES = frozenset(
    {
        TS_CREATED,
        TS_STARTING,
        TS_RUNNING,
        TS_STOPPING,
        TS_STOPPED,
        TS_COMPLETED,
        TS_FAILED,
    }
)

# ===== 播放模式 =====
PLAY_MODE_DIRECT_PLAY = "direct_play"
PLAY_MODE_DIRECT_STREAM = "direct_stream"
PLAY_MODE_TRANSCODE = "transcode"

# ===== 流协议 =====
STREAM_PROTOCOL_HLS = "hls"
STREAM_PROTOCOL_FMP4 = "fmp4"
STREAM_PROTOCOL_PROGRESSIVE = "progressive"


class PlaybackSession(BaseEntityMixin, Base):
    """一次播放器会话。

    由统一 Playback API 创建（POST /api/playback/sessions），
    记录服务端的播放决策结果，前端据此选择播放 URL。
    """

    __tablename__ = "playback_sessions"

    __table_args__ = (
        Index("ix_playback_sessions_status_active", "status", "deleted_at"),
        Index("ix_playback_sessions_mv_active", "music_video_id", "deleted_at"),
        Index("ix_playback_sessions_active_at", "last_active_at", "deleted_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    music_video_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("music_videos.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # 播放源文件绝对路径快照（决策时解析，避免后续路径漂移）
    source_path: Mapped[str] = mapped_column(String(1000))
    # direct_play / direct_stream / transcode
    play_mode: Mapped[str] = mapped_column(String(20), index=True)
    # 决策结果：是否必须启动转码
    transcode_required: Mapped[bool] = mapped_column(Boolean, default=False)

    # 决策输入：客户端请求的档位（original / 1080p ...），排查画质选择问题
    requested_quality: Mapped[Optional[str]] = mapped_column(String(20))
    # 决策原因（Direct Play / Direct Stream / Transcode 的依据，可读、供排查）
    decision_reason: Mapped[Optional[str]] = mapped_column(Text)

    # ===== 输出规格（决策后的目标）=====
    quality: Mapped[Optional[str]] = mapped_column(String(20))  # original / 1080p ...
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    video_bitrate: Mapped[Optional[int]] = mapped_column(Integer)  # bps
    audio_bitrate: Mapped[Optional[int]] = mapped_column(Integer)  # bps
    video_codec: Mapped[Optional[str]] = mapped_column(String(50))
    audio_codec: Mapped[Optional[str]] = mapped_column(String(50))
    container: Mapped[Optional[str]] = mapped_column(String(30))
    stream_protocol: Mapped[Optional[str]] = mapped_column(String(20))
    audio_stream_id: Mapped[Optional[int]] = mapped_column(Integer)
    subtitle_stream_id: Mapped[Optional[int]] = mapped_column(Integer)

    # 客户端能力快照（JSON），便于事后排查播放决策
    client_capabilities: Mapped[Optional[Any]] = mapped_column(JSON)

    # 关联的转码会话（transcode 模式时有值；多个 playback 可指向同一 transcode）
    transcode_session_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("transcode_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ===== 生命周期 =====
    status: Mapped[str] = mapped_column(String(20), default=PS_CREATED, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # 心跳时间：用于闲置回收
    last_active_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    transcode_session: Mapped[Optional["TranscodeSession"]] = relationship(
        "TranscodeSession", back_populates="playback_sessions"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PlaybackSession #{self.id} {self.play_mode} {self.status}>"


class TranscodeSession(BaseEntityMixin, Base):
    """一次实际转码会话，对应一个长期运行的 FFmpeg 进程。

    cache_key 唯一约束保证「相同源 + 相同 profile + 相同协议」的
    转码会话全局唯一，实现任务复用（多个播放器共享一个 FFmpeg）。
    """

    __tablename__ = "transcode_sessions"

    __table_args__ = (
        Index(
            "uq_transcode_sessions_cache_key_active",
            "cache_key",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index("ix_transcode_sessions_status_active", "status", "deleted_at"),
        Index("ix_transcode_sessions_active_at", "last_active_at", "deleted_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # 复用去重键：源文件绝对路径 + profile_key + 流协议 + 音轨/字幕
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    # 转码规格指纹（不含运行时硬件决策）
    profile_key: Mapped[str] = mapped_column(String(64), index=True)

    music_video_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("music_videos.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_path: Mapped[str] = mapped_column(String(1000))

    # ===== 输出规格 =====
    output_format: Mapped[str] = mapped_column(String(20), default="hls_mpegts")
    video_codec: Mapped[str] = mapped_column(String(50))
    audio_codec: Mapped[str] = mapped_column(String(50))
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    video_bitrate: Mapped[Optional[int]] = mapped_column(Integer)  # bps
    audio_bitrate: Mapped[Optional[int]] = mapped_column(Integer)  # bps
    framerate: Mapped[Optional[float]] = mapped_column(Float)

    # 实际使用的硬件加速方式：cpu / qsv / vaapi（由 HardwareAccelerationManager 落定）
    hardware_acceleration: Mapped[str] = mapped_column(String(20), default="cpu")

    # ===== 运行状态 =====
    status: Mapped[str] = mapped_column(String(20), default=TS_CREATED, index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    ffmpeg_pid: Mapped[Optional[int]] = mapped_column(Integer)
    exit_code: Mapped[Optional[int]] = mapped_column(Integer)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_active_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)

    # ===== 产物位置（相对 derived 目录）=====
    output_directory: Mapped[Optional[str]] = mapped_column(String(1000))
    playlist_path: Mapped[Optional[str]] = mapped_column(String(1000))

    # ===== 可观测性 =====
    full_command: Mapped[Optional[str]] = mapped_column(Text)
    stderr_tail: Mapped[Optional[str]] = mapped_column(Text)  # 截断的 FFmpeg 日志
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    playback_sessions: Mapped[List["PlaybackSession"]] = relationship(
        "PlaybackSession", back_populates="transcode_session"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TranscodeSession #{self.id} {self.status} {self.hardware_acceleration}>"
