"""Artist 相关 schema。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, List, Optional

from pydantic import Field

from app.schemas.common import EntityReadMixin, ORMModel


class ArtistBase(ORMModel):
    name: str
    sort_name: Optional[str] = None
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    stage_name: Optional[str] = None
    aliases: Optional[List[str]] = None
    gender: Optional[str] = None
    birth_date: Optional[date] = None
    birth_place: Optional[str] = None
    occupation: Optional[str] = None
    debut_date: Optional[date] = None
    social_media: Optional[Any] = None
    external_links: Optional[List[Any]] = None
    description: Optional[str] = None
    tagline: Optional[str] = None
    avatar_path: Optional[str] = None
    banner_path: Optional[str] = None
    hide_from_home: bool = False


class ArtistCreate(ArtistBase):
    # 组合成员导入等显式场景：同名成员各自成条是常态，置 true 跳过重名/近名拦截
    allow_name_conflict: bool = False


class ArtistUpdate(ORMModel):
    name: Optional[str] = None
    sort_name: Optional[str] = None
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    stage_name: Optional[str] = None
    aliases: Optional[List[str]] = None
    gender: Optional[str] = None
    birth_date: Optional[date] = None
    birth_place: Optional[str] = None
    occupation: Optional[str] = None
    debut_date: Optional[date] = None
    social_media: Optional[Any] = None
    external_links: Optional[List[Any]] = None
    description: Optional[str] = None
    tagline: Optional[str] = None
    avatar_path: Optional[str] = None
    banner_path: Optional[str] = None
    hide_from_home: Optional[bool] = None


class ArtistRead(ArtistBase, EntityReadMixin):
    id: int
    completion_pct: Optional[int] = None
    video_count: int = 0
    # 用于前端图片缓存键（换头像后自动失效）
    updated_at: Optional[datetime] = None


class ArtistBrief(EntityReadMixin):
    """列表/下拉用精简版。"""

    id: int
    name: str
    chinese_name: Optional[str] = None
    stage_name: Optional[str] = None
    avatar_path: Optional[str] = None


class ArtistFuzzyHit(ORMModel):
    """新建前查重用的模糊匹配命中项。"""

    id: int
    name: str
    chinese_name: Optional[str] = None
    stage_name: Optional[str] = None
    korean_name: Optional[str] = None
    score: float
