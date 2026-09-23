"""Artist（艺人）模型。

Solo Artist 与 Group 是平等的发行主体，因此 Artist 不依赖 Group 存在。
"""

from __future__ import annotations

from datetime import date
from typing import Any, List, Optional

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseEntityMixin
from app.core.database import Base


class Artist(BaseEntityMixin, Base):
    __tablename__ = "artists"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    sort_name: Mapped[Optional[str]] = mapped_column(String(200), index=True)

    # 多语言名 / 艺名
    chinese_name: Mapped[Optional[str]] = mapped_column(String(200))
    english_name: Mapped[Optional[str]] = mapped_column(String(200))
    korean_name: Mapped[Optional[str]] = mapped_column(String(200))
    stage_name: Mapped[Optional[str]] = mapped_column(String(200))

    # JSON 数组：别名
    aliases: Mapped[Optional[List[str]]] = mapped_column(JSON)

    # Male / Female / Other（用字符串保留扩展性）
    gender: Mapped[Optional[str]] = mapped_column(String(20))
    birth_date: Mapped[Optional[date]] = mapped_column()
    birth_place: Mapped[Optional[str]] = mapped_column(String(200))
    occupation: Mapped[Optional[str]] = mapped_column(String(200))
    debut_date: Mapped[Optional[date]] = mapped_column()

    # JSON：社交账号
    social_media: Mapped[Optional[Any]] = mapped_column(JSON)
    # JSON：外部链接
    external_links: Mapped[Optional[List[Any]]] = mapped_column(JSON)

    description: Mapped[Optional[str]] = mapped_column(Text)

    # 首页主舞台：AI 生成的一句话简介（≤40 字，适合大字排版下的小字说明）
    tagline: Mapped[Optional[str]] = mapped_column(String(500))
    # 头像/横幅的人脸焦点（0~1 归一化，用于 object-position 居中裁切；换图后置空重测）
    focus_x: Mapped[Optional[float]] = mapped_column()
    focus_y: Mapped[Optional[float]] = mapped_column()
    # 预留：官方艺术 logo（透明底 PNG，相对 derived）；本次只留字段不做 UI
    logo_path: Mapped[Optional[str]] = mapped_column(String(500))

    # 站点获取下载的本地头像（相对 derived 目录，如 avatars/artists/3.jpg）
    avatar_path: Mapped[Optional[str]] = mapped_column(String(500))
    # 手机详情顶栏横幅（相对 derived，如 banners/artists/3.jpg），与头像分裁
    banner_path: Mapped[Optional[str]] = mapped_column(String(500))

    # 资料库开关：不在首页主舞台轮动展示
    hide_from_home: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )

    # ===== 关系 =====
    memberships: Mapped[List["GroupMembership"]] = relationship(
        "GroupMembership",
        back_populates="artist",
        cascade="all, delete-orphan",
    )
    company_relations: Mapped[List["ArtistCompanyRelation"]] = relationship(
        "ArtistCompanyRelation",
        back_populates="artist",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Artist #{self.id} {self.name}>"
