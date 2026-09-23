"""Song（歌曲）及其关系：SongArtistRelation、Credits。

SongArtistRelation 约束：artist_id 与 group_id 恰有一个非空（应用层 + 部分索引保证）。
"""

from __future__ import annotations

from datetime import date
from typing import Any, List, Optional

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseEntityMixin, TimestampMixin
from app.core.database import Base


class Song(BaseEntityMixin, Base):
    __tablename__ = "songs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    chinese_name: Mapped[Optional[str]] = mapped_column(String(300))
    english_name: Mapped[Optional[str]] = mapped_column(String(300))
    korean_name: Mapped[Optional[str]] = mapped_column(String(300))
    aliases: Mapped[Optional[List[str]]] = mapped_column(JSON)

    release_date: Mapped[Optional[date]] = mapped_column(index=True)
    # Title / B-side / Intro / Outro / Interlude / Other
    song_type: Mapped[Optional[str]] = mapped_column(String(30))

    # 多态发行主体
    release_artist_type: Mapped[Optional[str]] = mapped_column(String(20))
    release_artist_id: Mapped[Optional[int]] = mapped_column(index=True)

    duration: Mapped[Optional[int]] = mapped_column(Integer)  # 秒
    description: Mapped[Optional[str]] = mapped_column(Text)
    external_links: Mapped[Optional[List[Any]]] = mapped_column(JSON)

    # ===== 关系 =====
    artist_relations: Mapped[List["SongArtistRelation"]] = relationship(
        "SongArtistRelation",
        back_populates="song",
        cascade="all, delete-orphan",
        order_by="SongArtistRelation.order",
    )
    credits: Mapped[List["Credits"]] = relationship(
        "Credits",
        back_populates="song",
        cascade="all, delete-orphan",
    )
    album_tracks: Mapped[List["AlbumTrack"]] = relationship(
        "AlbumTrack", back_populates="song"
    )
    music_videos: Mapped[List["MusicVideo"]] = relationship(
        "MusicVideo", back_populates="song"
    )

    @property
    def album_ids(self) -> List[int]:
        return [t.album_id for t in self.album_tracks]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Song #{self.id} {self.name}>"


class SongArtistRelation(Base):
    """歌曲与艺人/组合的关系。

    artist_id、group_id 恰有一个非空；role 表明该主体的角色。
    """

    __tablename__ = "song_artist_relations"
    __table_args__ = (
        # XOR 约束：两者必须有一个非空
        CheckConstraint(
            "(artist_id IS NOT NULL AND group_id IS NULL) OR "
            "(artist_id IS NULL AND group_id IS NOT NULL)",
            name="ck_song_artist_relation_xor",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    song_id: Mapped[int] = mapped_column(
        ForeignKey("songs.id", ondelete="CASCADE"), index=True
    )
    artist_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("artists.id", ondelete="CASCADE"), nullable=True
    )
    group_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), nullable=True
    )
    # PrimaryArtist / FeaturedArtist / Remixer / CoverArtist
    role: Mapped[str] = mapped_column(String(30), default="PrimaryArtist")
    order: Mapped[int] = mapped_column(Integer, default=0)

    song: Mapped["Song"] = relationship("Song", back_populates="artist_relations")
    artist: Mapped[Optional["Artist"]] = relationship("Artist")
    group: Mapped[Optional["Group"]] = relationship("Group")


class Credits(TimestampMixin, Base):
    """歌曲制作人员（可选）。"""

    __tablename__ = "credits"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    song_id: Mapped[int] = mapped_column(
        ForeignKey("songs.id", ondelete="CASCADE"), index=True
    )
    artist_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("artists.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200))
    # Composer / Lyricist / Producer / Arranger 等
    role: Mapped[str] = mapped_column(String(50))

    song: Mapped["Song"] = relationship("Song", back_populates="credits")
