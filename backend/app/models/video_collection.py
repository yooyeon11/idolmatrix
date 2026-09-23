"""视频收藏夹。收藏的是单个视频 uid，一张/一个视频可进多个夹。"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import BaseEntityMixin, TimestampMixin


class VideoCollection(BaseEntityMixin, Base):
    __tablename__ = "video_collections"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    items: Mapped[List["VideoCollectionItem"]] = relationship(
        "VideoCollectionItem",
        back_populates="collection",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<VideoCollection #{self.id} {self.name}>"


class VideoCollectionItem(TimestampMixin, Base):
    __tablename__ = "video_collection_items"
    __table_args__ = (
        Index("uq_video_collection_item", "collection_id", "music_video_id", unique=True),
        Index("ix_video_collection_video_uid", "video_uid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    collection_id: Mapped[int] = mapped_column(
        ForeignKey("video_collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    music_video_id: Mapped[int] = mapped_column(
        ForeignKey("music_videos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    video_uid: Mapped[str] = mapped_column(String(36), nullable=False)

    collection: Mapped["VideoCollection"] = relationship(
        "VideoCollection", back_populates="items"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<VideoCollectionItem col={self.collection_id} mv={self.music_video_id}>"
