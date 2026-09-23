"""公司关系编辑：组合-公司 / 艺人-公司 的增删改。

资料库工作台用。关系行含 role（如「经纪公司」）、status（Active/Inactive/Former）
与起止日期，均为应用层校验。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.deps import DbDep
from app.models.company import ArtistCompanyRelation, Company, GroupCompanyRelation
from app.models.artist import Artist
from app.models.group import Group
from app.schemas.company import (
    ArtistCompanyRelationCreate,
    ArtistCompanyRelationUpdate,
    CompanyRelationRead,
    GroupCompanyRelationCreate,
    GroupCompanyRelationUpdate,
)

router = APIRouter(prefix="/company-relations", tags=["company-relations"])

_STATUS_PATTERN = ("Active", "Inactive", "Former")


def _load_relation(db, model, relation_id: int):
    rel = db.get(model, relation_id)
    if rel is None:
        raise HTTPException(404, "关系不存在")
    return rel


def _to_read(rel, company_name: str) -> CompanyRelationRead:
    return CompanyRelationRead(
        id=rel.id,
        company_id=rel.company_id,
        company_name=company_name,
        role=rel.role,
        status=rel.status,
        start_date=rel.start_date,
        end_date=rel.end_date,
    )


def _company_name(db, company_id: int) -> str:
    c = db.get(Company, company_id)
    return c.name if c else "?"


def _check_status(status: str) -> None:
    if status not in _STATUS_PATTERN:
        raise HTTPException(400, f"status 须为 {'/'.join(_STATUS_PATTERN)}")


# ===== 组合-公司 =====


@router.post("/group", response_model=CompanyRelationRead, status_code=201)
def create_group_relation(payload: GroupCompanyRelationCreate, db: DbDep):
    if db.get(Group, payload.group_id) is None:
        raise HTTPException(404, "Group 不存在")
    if db.get(Company, payload.company_id) is None:
        raise HTTPException(404, "Company 不存在")
    _check_status(payload.status)
    rel = GroupCompanyRelation(
        group_id=payload.group_id,
        company_id=payload.company_id,
        role=payload.role,
        status=payload.status,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    db.add(rel)
    db.commit()
    db.refresh(rel)
    return _to_read(rel, _company_name(db, rel.company_id))


@router.patch("/group/{relation_id}", response_model=CompanyRelationRead)
def update_group_relation(relation_id: int, payload: GroupCompanyRelationUpdate, db: DbDep):
    rel = _load_relation(db, GroupCompanyRelation, relation_id)
    for field in ("role", "status", "start_date", "end_date"):
        if field in payload.model_fields_set:
            setattr(rel, field, getattr(payload, field))
    if rel.status is not None:
        _check_status(rel.status)
    db.commit()
    db.refresh(rel)
    return _to_read(rel, _company_name(db, rel.company_id))


@router.delete("/group/{relation_id}", status_code=204)
def delete_group_relation(relation_id: int, db: DbDep):
    rel = _load_relation(db, GroupCompanyRelation, relation_id)
    db.delete(rel)
    db.commit()


# ===== 艺人-公司 =====


@router.post("/artist", response_model=CompanyRelationRead, status_code=201)
def create_artist_relation(payload: ArtistCompanyRelationCreate, db: DbDep):
    if db.get(Artist, payload.artist_id) is None:
        raise HTTPException(404, "Artist 不存在")
    if db.get(Company, payload.company_id) is None:
        raise HTTPException(404, "Company 不存在")
    _check_status(payload.status)
    rel = ArtistCompanyRelation(
        artist_id=payload.artist_id,
        company_id=payload.company_id,
        role=payload.role,
        status=payload.status,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    db.add(rel)
    db.commit()
    db.refresh(rel)
    return _to_read(rel, _company_name(db, rel.company_id))


@router.patch("/artist/{relation_id}", response_model=CompanyRelationRead)
def update_artist_relation(relation_id: int, payload: ArtistCompanyRelationUpdate, db: DbDep):
    rel = _load_relation(db, ArtistCompanyRelation, relation_id)
    for field in ("role", "status", "start_date", "end_date"):
        if field in payload.model_fields_set:
            setattr(rel, field, getattr(payload, field))
    if rel.status is not None:
        _check_status(rel.status)
    db.commit()
    db.refresh(rel)
    return _to_read(rel, _company_name(db, rel.company_id))


@router.delete("/artist/{relation_id}", status_code=204)
def delete_artist_relation(relation_id: int, db: DbDep):
    rel = _load_relation(db, ArtistCompanyRelation, relation_id)
    db.delete(rel)
    db.commit()
