"""Album（专辑）模型。

release_artist_type + release_artist_id 为多态发行主体：
  - 'artist'  -> artists.id
  - 'group'   -> groups.id
不使用真实外键，由应用层校验，避免与多态语义冲突。
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseEntityMixin
from app.core.database import Base


class Album(BaseEntityMixin, Base):
    __tablename__ = "albums"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    chinese_name: Mapped[Optional[str]] = mapped_column(String(300))
    english_name: Mapped[Optional[str]] = mapped_column(String(300))
    korean_name: Mapped[Optional[str]] = mapped_column(String(300))
    aliases: Mapped[Optional[List[str]]] = mapped_column(JSON)

    release_date: Mapped[Optional[date]] = mapped_column(index=True)
    # Single / MiniAlbum / FullAlbum / Repackage / OST / Compilation / Other
    album_type: Mapped[Optional[str]] = mapped_column(String(30), index=True)

    # 多态发行主体：'artist' 或 'group'
    release_artist_type: Mapped[Optional[str]] = mapped_column(String(20))
    release_artist_id: Mapped[Optional[int]] = mapped_column(index=True)

    # 可选厂牌（关联 Company）
    label_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    description: Mapped[Optional[str]] = mapped_column(Text)

    # 本地封面相对路径（站点获取/上传下载到 derived/avatars/albums/）
    cover_path: Mapped[Optional[str]] = mapped_column(String(500))

    # ===== 关系 =====
    tracks: Mapped[List["AlbumTrack"]] = relationship(
        "AlbumTrack",
        back_populates="album",
        cascade="all, delete-orphan",
        order_by="AlbumTrack.disc_number, AlbumTrack.track_number",
    )
    label: Mapped[Optional["Company"]] = relationship("Company")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Album #{self.id} {self.name}>"


class AlbumTrack(Base):
    """专辑曲目：把 Song 组织进 Album 的关联表。

    不加 TimestampMixin：本表是纯关联，无需独立时间戳。
    """

    __tablename__ = "album_tracks"
    __table_args__ = (
        UniqueConstraint(
            "album_id", "disc_number", "track_number", name="uq_album_track_position"
        ),
        UniqueConstraint(
            "album_id", "song_id", name="uq_album_track_song"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    album_id: Mapped[int] = mapped_column(
        ForeignKey("albums.id", ondelete="CASCADE"), index=True
    )
    song_id: Mapped[int] = mapped_column(
        ForeignKey("songs.id", ondelete="CASCADE"), index=True
    )
    disc_number: Mapped[int] = mapped_column(Integer, default=1)
    track_number: Mapped[int] = mapped_column(Integer)

    album: Mapped["Album"] = relationship("Album", back_populates="tracks")
    song: Mapped["Song"] = relationship("Song", back_populates="album_tracks")
