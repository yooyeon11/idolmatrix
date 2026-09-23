"""Company（公司）及其与艺人/组合的关系。

Company 自关联 parent_company_id 支持母子公司层级。
ArtistCompanyRelation / GroupCompanyRelation 记录多对多关系与历史。
"""

from __future__ import annotations

from datetime import date
from typing import Any, List, Optional

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseEntityMixin, TimestampMixin
from app.core.database import Base


class Company(BaseEntityMixin, Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), index=True)

    chinese_name: Mapped[Optional[str]] = mapped_column(String(200))
    english_name: Mapped[Optional[str]] = mapped_column(String(200))
    korean_name: Mapped[Optional[str]] = mapped_column(String(200))
    aliases: Mapped[Optional[List[str]]] = mapped_column(JSON)

    # Agency / Label / Distributor / Subsidiary 等
    company_type: Mapped[Optional[str]] = mapped_column(String(50))
    parent_company_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    description: Mapped[Optional[str]] = mapped_column(Text)
    social_media: Mapped[Optional[Any]] = mapped_column(JSON)
    external_links: Mapped[Optional[List[Any]]] = mapped_column(JSON)

    # ===== 关系 =====
    parent: Mapped[Optional["Company"]] = relationship(
        "Company",
        remote_side="Company.id",
        back_populates="children",
    )
    children: Mapped[List["Company"]] = relationship(
        "Company", back_populates="parent"
    )

    artist_relations: Mapped[List["ArtistCompanyRelation"]] = relationship(
        "ArtistCompanyRelation",
        back_populates="company",
        cascade="all, delete-orphan",
    )
    group_relations: Mapped[List["GroupCompanyRelation"]] = relationship(
        "GroupCompanyRelation",
        back_populates="company",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Company #{self.id} {self.name}>"


class ArtistCompanyRelation(TimestampMixin, Base):
    __tablename__ = "artist_company_relations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    artist_id: Mapped[int] = mapped_column(
        ForeignKey("artists.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    # Active / Inactive / Former
    status: Mapped[str] = mapped_column(String(20), default="Active")
    start_date: Mapped[Optional[date]] = mapped_column()
    end_date: Mapped[Optional[date]] = mapped_column()
    role: Mapped[Optional[str]] = mapped_column(String(100))

    artist: Mapped["Artist"] = relationship("Artist", back_populates="company_relations")
    company: Mapped["Company"] = relationship(
        "Company", back_populates="artist_relations"
    )


class GroupCompanyRelation(TimestampMixin, Base):
    __tablename__ = "group_company_relations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="Active")
    start_date: Mapped[Optional[date]] = mapped_column()
    end_date: Mapped[Optional[date]] = mapped_column()
    role: Mapped[Optional[str]] = mapped_column(String(100))

    group: Mapped["Group"] = relationship("Group", back_populates="company_relations")
    company: Mapped["Company"] = relationship(
        "Company", back_populates="group_relations"
    )
