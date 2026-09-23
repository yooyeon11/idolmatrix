"""图片来源文件夹与照片索引。

照片文件留在原目录，本表只做索引。每张照片有独立 uid，帖子仅用于展示分组。
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import BaseEntityMixin, TimestampMixin

PHOTO_SECTIONS = ("official", "fan", "wall")
PHOTO_OWNER_TYPES = ("artist", "group")
PHOTO_MEDIA_KINDS = ("image", "video")
PHOTO_PROVIDERS = ("folder", "mtphotos")


class PhotoSource(TimestampMixin, Base):
    """艺人/组合绑定的一个本地图片文件夹，对应一个展示分区。"""

    __tablename__ = "photo_sources"
    __table_args__ = (
        Index(
            "uq_photo_source_owner_folder",
            "owner_type",
            "owner_id",
            "folder_path",
            unique=True,
        ),
        Index("ix_photo_source_owner_section", "owner_type", "owner_id", "section"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    owner_type: Mapped[str] = mapped_column(String(20), nullable=False)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    section: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    folder_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    provider: Mapped[str] = mapped_column(
        String(20), nullable=False, default="folder", server_default="folder"
    )
    external_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    recursive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_scanned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_scan_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_scan_posts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    photos: Mapped[List["Photo"]] = relationship(
        "Photo",
        back_populates="source",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PhotoSource #{self.id} {self.owner_type}/{self.owner_id} {self.section}>"


class Photo(BaseEntityMixin, Base):
    """单张照片/短视频索引。file_path 指向磁盘原文件，不搬家。"""

    __tablename__ = "photos"
    __table_args__ = (
        Index(
            "uq_photo_source_path_active",
            "source_id",
            "file_path",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index("ix_photo_source_post", "source_id", "post_key"),
        Index("ix_photo_published", "published_at"),
        Index("ix_photo_analysis_scene", "analysis_scene"),
        Index("ix_photo_analysis_shoes", "analysis_shoes_type"),
        Index("ix_photo_analysis_hosiery", "analysis_hosiery_present"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("photo_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    media_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="image")
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    post_key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True, index=True)
    position_in_post: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    analysis: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    analysis_prompt_version: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    analyzed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    analysis_scene: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    analysis_shot: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    analysis_shoes_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    analysis_hosiery_present: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    analysis_hosiery_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    source: Mapped["PhotoSource"] = relationship("PhotoSource", back_populates="photos")
    collection_items: Mapped[List["PhotoCollectionItem"]] = relationship(
        "PhotoCollectionItem",
        back_populates="photo",
        cascade="all, delete-orphan",
    )

    @property
    def provider(self) -> str:
        source = self.source
        if source is not None and source.provider:
            return source.provider
        return "folder"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Photo #{self.id} {self.file_name}>"


class PhotoCollection(BaseEntityMixin, Base):
    """收藏夹。收藏的是单张照片 uid，与帖子分组无关。"""

    __tablename__ = "photo_collections"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    items: Mapped[List["PhotoCollectionItem"]] = relationship(
        "PhotoCollectionItem",
        back_populates="collection",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PhotoCollection #{self.id} {self.name}>"


class PhotoCollectionItem(TimestampMixin, Base):
    """收藏夹中的一张照片。photo_uid 为长期引用。"""

    __tablename__ = "photo_collection_items"
    __table_args__ = (
        Index(
            "uq_photo_collection_item",
            "collection_id",
            "photo_id",
            unique=True,
        ),
        Index("ix_photo_collection_photo_uid", "photo_uid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    collection_id: Mapped[int] = mapped_column(
        ForeignKey("photo_collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    photo_id: Mapped[int] = mapped_column(
        ForeignKey("photos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    photo_uid: Mapped[str] = mapped_column(String(36), nullable=False)

    collection: Mapped["PhotoCollection"] = relationship(
        "PhotoCollection", back_populates="items"
    )
    photo: Mapped["Photo"] = relationship("Photo", back_populates="collection_items")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PhotoCollectionItem col={self.collection_id} photo={self.photo_id}>"
