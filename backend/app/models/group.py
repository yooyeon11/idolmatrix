"""Group（组合）模型。

表名使用 `groups` 避免 SQL 保留字 GROUP。
"""

from __future__ import annotations

from datetime import date
from typing import Any, List, Optional

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseEntityMixin
from app.core.database import Base


class Group(BaseEntityMixin, Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    sort_name: Mapped[Optional[str]] = mapped_column(String(200), index=True)

    chinese_name: Mapped[Optional[str]] = mapped_column(String(200))
    english_name: Mapped[Optional[str]] = mapped_column(String(200))
    korean_name: Mapped[Optional[str]] = mapped_column(String(200))

    aliases: Mapped[Optional[List[str]]] = mapped_column(JSON)

    # Girl Group / Boy Group / Co-ed / Project / Sub-unit 等
    group_type: Mapped[Optional[str]] = mapped_column(String(50))
    # 上级组合：小分队（Sub-unit）指向完整体，如 EVOlution -> tripleS
    parent_group_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("groups.id"), index=True
    )
    # Female / Male / Mixed
    gender_type: Mapped[Optional[str]] = mapped_column(String(20))
    origin_country: Mapped[Optional[str]] = mapped_column(String(100))

    debut_date: Mapped[Optional[date]] = mapped_column()
    description: Mapped[Optional[str]] = mapped_column(Text)

    # 首页主舞台：AI 生成的一句话简介（≤40 字，适合大字排版下的小字说明）
    tagline: Mapped[Optional[str]] = mapped_column(String(500))
    # 头像/横幅的人脸焦点（0~1 归一化，用于 object-position 居中裁切；换图后置空重测）
    focus_x: Mapped[Optional[float]] = mapped_column()
    focus_y: Mapped[Optional[float]] = mapped_column()
    # 预留：官方艺术 logo（透明底 PNG，相对 derived）；本次只留字段不做 UI
    logo_path: Mapped[Optional[str]] = mapped_column(String(500))

    # 站点获取下载的本地头像（相对 derived 目录，如 avatars/groups/5.jpg）
    avatar_path: Mapped[Optional[str]] = mapped_column(String(500))
    # 手机详情顶栏横幅（相对 derived，如 banners/groups/5.jpg），与头像分裁
    banner_path: Mapped[Optional[str]] = mapped_column(String(500))

    # 资料库开关：不在首页主舞台轮动展示
    hide_from_home: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )

    social_media: Mapped[Optional[Any]] = mapped_column(JSON)
    external_links: Mapped[Optional[List[Any]]] = mapped_column(JSON)

    # ===== 关系 =====
    memberships: Mapped[List["GroupMembership"]] = relationship(
        "GroupMembership",
        back_populates="group",
        cascade="all, delete-orphan",
    )
    company_relations: Mapped[List["GroupCompanyRelation"]] = relationship(
        "GroupCompanyRelation",
        back_populates="group",
        cascade="all, delete-orphan",
    )
    # 自引用：上级完整体 / 旗下小分队（软删除过滤由查询层负责）
    parent: Mapped[Optional["Group"]] = relationship(
        "Group",
        remote_side="Group.id",
        primaryjoin="Group.parent_group_id == Group.id",
        overlaps="sub_units",
    )
    sub_units: Mapped[List["Group"]] = relationship(
        "Group",
        primaryjoin="Group.parent_group_id == Group.id",
        overlaps="parent",
    )

    @property
    def company_ids(self) -> List[int]:
        return [r.company_id for r in self.company_relations]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Group #{self.id} {self.name}>"
