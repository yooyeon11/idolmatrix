"""Company 及关系 schema。"""

from __future__ import annotations

from datetime import date
from typing import Any, List, Optional

from app.schemas.common import EntityReadMixin, ORMModel


class CompanyBase(ORMModel):
    name: str
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    aliases: Optional[List[str]] = None
    company_type: Optional[str] = None
    parent_company_id: Optional[int] = None
    description: Optional[str] = None
    social_media: Optional[Any] = None
    external_links: Optional[List[Any]] = None


class CompanyCreate(CompanyBase):
    pass


class CompanyUpdate(ORMModel):
    name: Optional[str] = None
    chinese_name: Optional[str] = None
    english_name: Optional[str] = None
    korean_name: Optional[str] = None
    aliases: Optional[List[str]] = None
    company_type: Optional[str] = None
    parent_company_id: Optional[int] = None
    description: Optional[str] = None
    social_media: Optional[Any] = None
    external_links: Optional[List[Any]] = None


class CompanyRead(CompanyBase, EntityReadMixin):
    id: int
    completion_pct: Optional[int] = None


class CompanyBrief(EntityReadMixin):
    id: int
    name: str
    chinese_name: Optional[str] = None
    company_type: Optional[str] = None


# ===== 关系 =====
class ArtistCompanyRelationBase(ORMModel):
    artist_id: int
    company_id: int
    status: str = "Active"
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    role: Optional[str] = None


class ArtistCompanyRelationCreate(ArtistCompanyRelationBase):
    pass


class ArtistCompanyRelationRead(ArtistCompanyRelationBase):
    id: int


class GroupCompanyRelationBase(ORMModel):
    group_id: int
    company_id: int
    status: str = "Active"
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    role: Optional[str] = None


class GroupCompanyRelationCreate(GroupCompanyRelationBase):
    pass


class GroupCompanyRelationRead(GroupCompanyRelationBase):
    id: int


class CompanyRelationMember(ORMModel):
    id: int
    name: str
    chinese_name: Optional[str] = None
    korean_name: Optional[str] = None
    status: str = "Active"
    role: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class CompanyRelationsRead(ORMModel):
    company_id: int
    company_name: str
    artists: List[CompanyRelationMember] = []
    groups: List[CompanyRelationMember] = []


# ===== 公司关系（组合-公司 / 艺人-公司）编辑 =====


class GroupCompanyRelationCreate(ORMModel):
    group_id: int
    company_id: int
    role: Optional[str] = None
    status: str = "Active"
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class GroupCompanyRelationUpdate(ORMModel):
    role: Optional[str] = None
    status: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class ArtistCompanyRelationCreate(ORMModel):
    artist_id: int
    company_id: int
    role: Optional[str] = None
    status: str = "Active"
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class ArtistCompanyRelationUpdate(ORMModel):
    role: Optional[str] = None
    status: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class CompanyRelationRead(ORMModel):
    id: int
    company_id: int
    company_name: str
    role: Optional[str] = None
    status: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
