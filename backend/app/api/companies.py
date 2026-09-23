"""Company 路由：CRUD + 关系管理。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.guards import require_active
from app.core.deps import DbDep, PaginationDep
from app.services.soft_delete_service import soft_delete_company
from app.models.artist import Artist
from app.models.company import (
    ArtistCompanyRelation,
    Company,
    GroupCompanyRelation,
)
from app.models.group import Group
from app.schemas import (
    ArtistCompanyRelationCreate,
    ArtistCompanyRelationRead,
    CompanyBrief,
    CompanyCreate,
    CompanyRead,
    CompanyRelationMember,
    CompanyRelationsRead,
    CompanyUpdate,
    GroupCompanyRelationCreate,
    GroupCompanyRelationRead,
    PageResponse,
)
from app.services.completion import company_completion_pct

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("", response_model=PageResponse[CompanyRead])
def list_companies(
    db: DbDep,
    pagination: PaginationDep,
    q: Optional[str] = Query(None),
    sort: Optional[str] = Query(None, description="排序：completion=完成度升序，-completion=降序"),
):
    completion = company_completion_pct()
    stmt = select(Company, completion.label("completion_pct")).where(Company.active_filter())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            Company.name.ilike(like) | Company.chinese_name.ilike(like)
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    if sort == "completion":
        stmt = stmt.order_by(completion.asc())
    elif sort == "-completion":
        stmt = stmt.order_by(completion.desc())
    else:
        stmt = stmt.order_by(Company.name)
    rows = db.execute(
        stmt.offset(pagination.offset).limit(pagination.limit)
    ).all()
    items = []
    for company, pct in rows:
        read = CompanyRead.model_validate(company)
        read.completion_pct = round(pct)
        items.append(read)
    return PageResponse(
        items=items, total=total, page=pagination.page, page_size=pagination.page_size
    )


@router.get("/brief", response_model=list[CompanyBrief])
def list_brief(db: DbDep, q: Optional[str] = None):
    stmt = select(Company).where(Company.active_filter())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Company.name.ilike(like) | Company.chinese_name.ilike(like))
    return db.scalars(stmt.order_by(Company.name).limit(50)).all()


@router.get("/by-uid/{uid}", response_model=CompanyRead)
def get_company_by_uid(uid: str, db: DbDep):
    """按永久业务 uid 查询；已软删除的实体默认不可见。"""
    company = Company.get_by_uid(db, uid)
    if not company:
        raise HTTPException(404, "Company 不存在")
    return company


@router.post("", response_model=CompanyRead, status_code=201)
def create_company(payload: CompanyCreate, db: DbDep):
    c = Company(**payload.model_dump())
    if c.parent_company_id is not None:
        require_active(db, Company, c.parent_company_id, "Company(parent)")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@router.get("/{company_id}", response_model=CompanyRead)
def get_company(company_id: int, db: DbDep):
    company = Company.get_active(db, company_id)
    if not company:
        raise HTTPException(404, "Company 不存在")
    return company


@router.patch("/{company_id}", response_model=CompanyRead)
def update_company(company_id: int, payload: CompanyUpdate, db: DbDep):
    company = Company.get_active(db, company_id)
    if not company:
        raise HTTPException(404, "Company 不存在")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(company, k, v)
    if company.parent_company_id is not None:
        require_active(db, Company, company.parent_company_id, "Company(parent)")
    db.commit()
    db.refresh(company)
    return company


@router.delete("/{company_id}", status_code=204)
def delete_company(company_id: int, db: DbDep):
    """软删除公司：写入 deleted_at 墓碑，并拆除艺人/组合对公司的关系行。

    恢复（回收站）只还原实体本身；已拆除的关系不会自动重建，需重新挂接。
    """
    company = Company.get_active(db, company_id)
    if not company:
        raise HTTPException(404, "Company 不存在")
    soft_delete_company(db, company)
    db.commit()


@router.get("/{company_id}/relations", response_model=CompanyRelationsRead)
def get_company_relations(company_id: int, db: DbDep):
    """查询公司当前的艺人与组合关联。"""
    company = Company.get_active(db, company_id)
    if not company:
        raise HTTPException(404, "Company 不存在")

    artist_rows = db.execute(
        select(ArtistCompanyRelation, Artist)
        .join(Artist, Artist.id == ArtistCompanyRelation.artist_id)
        .where(
            ArtistCompanyRelation.company_id == company_id,
            Artist.deleted_at.is_(None),
        )
        .order_by(Artist.name)
    ).all()
    artists = [
        CompanyRelationMember(
            id=a.id,
            name=a.name,
            chinese_name=a.chinese_name,
            korean_name=a.korean_name,
            status=r.status,
            role=r.role,
            start_date=r.start_date,
            end_date=r.end_date,
        )
        for r, a in artist_rows
    ]

    group_rows = db.execute(
        select(GroupCompanyRelation, Group)
        .join(Group, Group.id == GroupCompanyRelation.group_id)
        .where(
            GroupCompanyRelation.company_id == company_id,
            Group.deleted_at.is_(None),
        )
        .order_by(Group.name)
    ).all()
    groups = [
        CompanyRelationMember(
            id=g.id,
            name=g.name,
            chinese_name=g.chinese_name,
            korean_name=g.korean_name,
            status=r.status,
            role=r.role,
            start_date=r.start_date,
            end_date=r.end_date,
        )
        for r, g in group_rows
    ]

    return CompanyRelationsRead(
        company_id=company.id,
        company_name=company.name,
        artists=artists,
        groups=groups,
    )


# ===== ArtistCompanyRelation =====
@router.post(
    "/artist-relations",
    response_model=ArtistCompanyRelationRead,
    tags=["company-relations"],
    status_code=201,
)
def create_artist_relation(payload: ArtistCompanyRelationCreate, db: DbDep):
    require_active(db, Artist, payload.artist_id, "Artist")
    require_active(db, Company, payload.company_id, "Company")
    r = ArtistCompanyRelation(**payload.model_dump())
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


# ===== GroupCompanyRelation =====
@router.post(
    "/group-relations",
    response_model=GroupCompanyRelationRead,
    tags=["company-relations"],
    status_code=201,
)
def create_group_relation(payload: GroupCompanyRelationCreate, db: DbDep):
    require_active(db, Group, payload.group_id, "Group")
    require_active(db, Company, payload.company_id, "Company")
    r = GroupCompanyRelation(**payload.model_dump())
    db.add(r)
    db.commit()
    db.refresh(r)
    return r
