"""Group 相关 schema。"""

from __future__ import annotations

from datetime import date
from typing import Any, List, Optional

from pydantic import Field

from app.schemas.common import EntityReadMixin, ORMModel


class GroupBase(ORMModel):
    name: str
    sort_name: Optional[str] = None
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    aliases: Optional[List[str]] = None
    group_type: Optional[str] = Field(
        default=None, description="Girl Group / Boy Group / Co-ed / Project / Sub-unit"
    )
    parent_group_id: Optional[int] = Field(
        default=None, description="上级组合（小分队归属的完整体）"
    )
    gender_type: Optional[str] = None
    origin_country: Optional[str] = None
    debut_date: Optional[date] = None
    description: Optional[str] = None
    tagline: Optional[str] = None
    social_media: Optional[Any] = None
    external_links: Optional[List[Any]] = None
    avatar_path: Optional[str] = None
    banner_path: Optional[str] = None
    hide_from_home: bool = False


class GroupCreate(GroupBase):
    pass


class GroupUpdate(ORMModel):
    name: Optional[str] = None
    sort_name: Optional[str] = None
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    aliases: Optional[List[str]] = None
    group_type: Optional[str] = None
    parent_group_id: Optional[int] = None
    gender_type: Optional[str] = None
    origin_country: Optional[str] = None
    debut_date: Optional[date] = None
    description: Optional[str] = None
    tagline: Optional[str] = None
    social_media: Optional[Any] = None
    external_links: Optional[List[Any]] = None
    avatar_path: Optional[str] = None
    banner_path: Optional[str] = None
    company_ids: Optional[List[int]] = None
    hide_from_home: Optional[bool] = None


class GroupSubUnitBrief(EntityReadMixin):
    """上级组合详情里的旗下小分队简要。"""

    id: int
    name: str
    chinese_name: Optional[str] = None
    group_type: Optional[str] = None


class GroupRead(GroupBase, EntityReadMixin):
    id: int
    member_count: Optional[int] = None
    company_ids: List[int] = []
    completion_pct: Optional[int] = None
    # 详情接口填充；上级名称与旗下小分队列表
    parent_name: Optional[str] = None
    parent_uid: Optional[str] = None
    sub_units: List[GroupSubUnitBrief] = []


class GroupBrief(EntityReadMixin):
    id: int
    name: str
    chinese_name: Optional[str] = None
    group_type: Optional[str] = None
    avatar_path: Optional[str] = None


class GroupFuzzyHit(ORMModel):
    """新建前查重用的模糊匹配命中项。"""

    id: int
    name: str
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    score: float
