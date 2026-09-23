"""GroupMembership 相关 schema。"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from app.schemas.common import ORMModel


class MembershipBase(ORMModel):
    group_id: int
    artist_id: int
    join_date: Optional[date] = None
    leave_date: Optional[date] = None
    status: str = "Active"
    positions: Optional[List[str]] = None


class MembershipCreate(MembershipBase):
    pass


class MembershipUpdate(ORMModel):
    join_date: Optional[date] = None
    leave_date: Optional[date] = None
    status: Optional[str] = None
    positions: Optional[List[str]] = None


class MembershipRead(MembershipBase):
    id: int
    group_id: int
    artist_id: int


class MembershipReadWithArtist(MembershipRead):
    """携带艺人简要信息，用于组合详情页。"""

    artist_name: Optional[str] = None
    artist_chinese_name: Optional[str] = None
    artist_korean_name: Optional[str] = None
    artist_stage_name: Optional[str] = None
    artist_avatar_path: Optional[str] = None
    artist_uid: Optional[str] = None


class MembershipReadWithGroup(MembershipRead):
    """携带组合简要信息，用于艺人详情页。"""

    group_name: Optional[str] = None
    group_chinese_name: Optional[str] = None
    group_type: Optional[str] = None
    group_uid: Optional[str] = None
    # 母队溯源：该组合沿 parent_group_id 向上追踪到的顶级完整体。
    # 仅当存在上级（小分队指向母队）时填充，否则为空（等于自身）。
    root_group_name: Optional[str] = None
    root_group_chinese_name: Optional[str] = None
    root_group_type: Optional[str] = None
    root_group_uid: Optional[str] = None
