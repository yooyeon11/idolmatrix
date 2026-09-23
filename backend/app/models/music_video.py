"""MusicVideo（音乐视频 / 直拍）—— 项目最核心实体。

设计要点：
1. 技术字段由 ffprobe 写入，不靠人工猜测。
2. file_hash 用于去重。
3. ingestion_status 区分「待整理 / 已入库 / 归档」。
4. video_type 覆盖 K-pop 场景：OfficialMV / PersonalFancam / GroupFancam 等。
5. subject_artist_id 仅 PersonalFancam / OfficialFancam / OfficialFacecam 等需要指向具体成员时使用。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, List, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseEntityMixin
from app.core.database import Base

# ===== 多对多关联表：一个视频可关联多首歌曲 / 多个专辑 / 多位艺人 / 多个组合 =====
music_video_songs = Table(
    "music_video_songs",
    Base.metadata,
    Column(
        "music_video_id",
        ForeignKey("music_videos.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "song_id",
        ForeignKey("songs.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

music_video_albums = Table(
    "music_video_albums",
    Base.metadata,
    Column(
        "music_video_id",
        ForeignKey("music_videos.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "album_id",
        ForeignKey("albums.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

music_video_artists = Table(
    "music_video_artists",
    Base.metadata,
    Column(
        "music_video_id",
        ForeignKey("music_videos.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "artist_id",
        ForeignKey("artists.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

music_video_groups = Table(
    "music_video_groups",
    Base.metadata,
    Column(
        "music_video_id",
        ForeignKey("music_videos.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "group_id",
        ForeignKey("groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

music_video_track_albums = Table(
    "music_video_track_albums",
    Base.metadata,
    Column(
        "track_id",
        ForeignKey("music_video_tracks.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "album_id",
        ForeignKey("albums.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class MusicVideoTrack(Base):
    """视频曲目行：有序，专辑挂在这一行上（本视频语境），不替代歌曲目录。"""

    __tablename__ = "music_video_tracks"
    __table_args__ = (
        UniqueConstraint(
            "music_video_id", "position", name="uq_mv_track_position"
        ),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    music_video_id: Mapped[int] = mapped_column(
        ForeignKey("music_videos.id", ondelete="CASCADE"), index=True
    )
    song_id: Mapped[int] = mapped_column(
        ForeignKey("songs.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)

    music_video: Mapped["MusicVideo"] = relationship(
        "MusicVideo", back_populates="video_tracks"
    )
    song: Mapped["Song"] = relationship("Song")
    albums: Mapped[List["Album"]] = relationship(
        "Album", secondary=music_video_track_albums
    )


class MusicVideo(BaseEntityMixin, Base):
    __tablename__ = "music_videos"

    # 业务身份唯一约束（仅对活跃行生效；软删除行豁免，允许恢复/重建）：
    # - file_hash：本地文件去重
    # - (source_platform, source_id)：外部平台视频去重
    __table_args__ = (
        Index(
            "uq_music_videos_file_hash_active",
            "file_hash",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_music_videos_source_active",
            "source_platform",
            "source_id",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # ===== 基础元数据 =====
    name: Mapped[str] = mapped_column(String(300), index=True)
    original_title: Mapped[Optional[str]] = mapped_column(String(500))
    sort_title: Mapped[Optional[str]] = mapped_column(String(300))
    chinese_name: Mapped[Optional[str]] = mapped_column(String(300))
    english_name: Mapped[Optional[str]] = mapped_column(String(300))
    korean_name: Mapped[Optional[str]] = mapped_column(String(300))
    aliases: Mapped[Optional[List[str]]] = mapped_column(JSON)

    # ===== 关联 =====
    # 派生缓存：曲目第一首。读写以 video_tracks 为准。
    song_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("songs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 主类型（= video_types[0]），保留列方便索引和统计
    video_type: Mapped[str] = mapped_column(String(30), default="Other", index=True)
    # 类型唯一来源（可多选）；写入时同步 video_type
    video_types: Mapped[Optional[List[str]]] = mapped_column(JSON)
    # PersonalFancam 等指向具体成员
    subject_artist_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("artists.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # ===== 事件 / 时间 =====
    event_name: Mapped[Optional[str]] = mapped_column(String(200))
    performance_date: Mapped[Optional[date]] = mapped_column(index=True)
    # 舞台类型由 AI 辅助判断后写入 event_name；is_short 手动标记，未标记时按短视频/时长自动判断
    is_short: Mapped[Optional[bool]] = mapped_column(Boolean, default=None)
    # 是否为 solo 表演（表演者为独立艺人而非组合表演），由 AI 辅助判断，供自动整理入库使用
    is_solo: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    release_date: Mapped[Optional[date]] = mapped_column(index=True)
    published_date: Mapped[Optional[date]] = mapped_column()

    # ===== 来源 =====
    source_platform: Mapped[Optional[str]] = mapped_column(String(50))
    source_id: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(500))
    original_uploader: Mapped[Optional[str]] = mapped_column(String(200))

    # ===== 技术字段（由 ffprobe 写入）=====
    duration: Mapped[Optional[int]] = mapped_column(Integer)  # 秒
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    video_codec: Mapped[Optional[str]] = mapped_column(String(50))
    audio_codec: Mapped[Optional[str]] = mapped_column(String(50))
    frame_rate: Mapped[Optional[float]] = mapped_column()
    bitrate: Mapped[Optional[int]] = mapped_column(Integer)  # bps
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger)  # 字节

    # ===== 文件信息 =====
    file_name: Mapped[Optional[str]] = mapped_column(String(500))
    file_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    # 相对 storage_library_dir 的路径；incoming 阶段则为相对 incoming_dir
    file_path: Mapped[Optional[str]] = mapped_column(String(1000))
    # 派生产物路径（相对 storage_derived_dir）；缩略图以外不再存放永久转码产物，
    # 实时播放转码统一走 TranscodeCache（derived/transcodes/{video_id}/{profile_key}/）
    thumbnail_path: Mapped[Optional[str]] = mapped_column(String(1000))
    # 用户手动选帧指定的封面：为 True 时自动刷新（sidecar/指纹重做）不再覆盖
    cover_manual: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0")
    )
    # 封面焦点（0~1 归一化的人脸质心；封面重做时置空重新检测）
    focus_x: Mapped[Optional[float]] = mapped_column()
    focus_y: Mapped[Optional[float]] = mapped_column()

    # ===== 入库状态 =====
    # incoming（待整理）/ library（已入库）/ archived（归档）
    ingestion_status: Mapped[str] = mapped_column(
        String(20), default="incoming", index=True
    )

    external_links: Mapped[Optional[List[Any]]] = mapped_column(JSON)
    description: Mapped[Optional[str]] = mapped_column(Text)
    # 中文简介：由 AI 根据视频内容生成（原 description 为来源原文，保持只读）
    chinese_description: Mapped[Optional[str]] = mapped_column(Text)

    # ===== 关系 =====
    song: Mapped[Optional["Song"]] = relationship("Song", back_populates="music_videos")
    subject_artist: Mapped[Optional["Artist"]] = relationship("Artist")
    # 多对多：一个视频可关联多首歌曲 / 多个专辑 / 多位艺人 / 多个组合
    songs: Mapped[List["Song"]] = relationship(
        "Song", secondary=music_video_songs
    )
    albums: Mapped[List["Album"]] = relationship(
        "Album", secondary=music_video_albums
    )
    video_tracks: Mapped[List["MusicVideoTrack"]] = relationship(
        "MusicVideoTrack",
        back_populates="music_video",
        cascade="all, delete-orphan",
        order_by="MusicVideoTrack.position",
    )
    artists: Mapped[List["Artist"]] = relationship(
        "Artist", secondary=music_video_artists
    )
    groups: Mapped[List["Group"]] = relationship(
        "Group", secondary=music_video_groups
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MusicVideo #{self.id} {self.name} [{self.video_type}]>"
