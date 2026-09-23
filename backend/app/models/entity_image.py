"""EntityImage：艺人/组合的头像与横幅候选历史（TMDB 式多图管理）。

内部技术表：不做软删除、无 uid。主图由实体上的指针字段
（avatar_path / banner_path）决定，本表只保存候选池，
主图判定 = 指针值与候选行 rel_path 比对（不存 is_primary 字段）。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class EntityImage(TimestampMixin, Base):
    __tablename__ = "entity_images"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "kind",
            "rel_path",
            name="uq_entity_images_path",
        ),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # artist / group
    entity_type: Mapped[str] = mapped_column(String(16), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    # avatar / banner
    kind: Mapped[str] = mapped_column(String(16), index=True)
    # 相对 derived 的路径，如 avatars/artists/3_1.jpg
    rel_path: Mapped[str] = mapped_column(String(500))
    # upload / site / crop / existing（existing=存量主图惰性补录）
    source: Mapped[str] = mapped_column(String(16))
    width: Mapped[Optional[int]] = mapped_column()
    height: Mapped[Optional[int]] = mapped_column()
    # 图片内容 sha256 前 16 位，用于重复上传去重
    content_hash: Mapped[Optional[str]] = mapped_column(String(16), index=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<EntityImage #{self.id} {self.entity_type}/{self.entity_id}/"
            f"{self.kind} {self.rel_path}>"
        )
