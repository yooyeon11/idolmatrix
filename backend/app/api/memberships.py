"""GroupMembership 路由：管理组合成员关系。"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.guards import require_active
from app.core.deps import DbDep, PaginationDep
from app.models.artist import Artist
from app.models.group import Group
from app.models.membership import GroupMembership
from app.schemas import (
    MembershipCreate,
    MembershipRead,
    MembershipReadWithArtist,
    MembershipReadWithGroup,
    MembershipUpdate,
    PageResponse,
)
from app.services.group_tree import root_group

router = APIRouter(prefix="/memberships", tags=["memberships"])


@router.get("", response_model=PageResponse[MembershipRead])
def list_memberships(
    db: DbDep,
    pagination: PaginationDep,
    group_id: Optional[int] = Query(None),
    artist_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
):
    if group_id is not None:
        require_active(db, Group, group_id, "Group")
    if artist_id is not None:
        require_active(db, Artist, artist_id, "Artist")
    stmt = (
        select(GroupMembership)
        .join(Artist, GroupMembership.artist_id == Artist.id)
        .join(Group, GroupMembership.group_id == Group.id)
        .where(Artist.deleted_at.is_(None), Group.deleted_at.is_(None))
    )
    if group_id:
        stmt = stmt.where(GroupMembership.group_id == group_id)
    if artist_id:
        stmt = stmt.where(GroupMembership.artist_id == artist_id)
    if status:
        stmt = stmt.where(GroupMembership.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = (
        db.scalars(
            stmt.order_by(GroupMembership.join_date.desc())
            .offset(pagination.offset)
            .limit(pagination.limit)
        )
        .all()
    )
    return PageResponse(
        items=items, total=total, page=pagination.page, page_size=pagination.page_size
    )


@router.get("/by_group/{group_id}", response_model=List[MembershipReadWithArtist])
def list_by_group(group_id: int, db: DbDep, status: Optional[str] = Query(None)):
    require_active(db, Group, group_id, "Group")
    stmt = (
        select(GroupMembership, Artist)
        .join(Artist, GroupMembership.artist_id == Artist.id)
        .where(GroupMembership.group_id == group_id, Artist.deleted_at.is_(None))
    )
    if status:
        stmt = stmt.where(GroupMembership.status == status)
    rows = db.execute(stmt.order_by(GroupMembership.join_date.asc())).all()
    out = []
    for m, a in rows:
        item = MembershipReadWithArtist.model_validate(m)
        item.artist_name = a.name
        item.artist_chinese_name = a.chinese_name
        item.artist_korean_name = a.korean_name
        item.artist_stage_name = a.stage_name
        item.artist_avatar_path = a.avatar_path
        item.artist_uid = a.uid
        out.append(item)
    return out


@router.get("/by_artist/{artist_id}", response_model=List[MembershipReadWithGroup])
def list_by_artist(artist_id: int, db: DbDep, status: Optional[str] = Query(None)):
    require_active(db, Artist, artist_id, "Artist")
    stmt = (
        select(GroupMembership, Group)
        .join(Group, GroupMembership.group_id == Group.id)
        .where(GroupMembership.artist_id == artist_id, Group.deleted_at.is_(None))
    )
    if status:
        stmt = stmt.where(GroupMembership.status == status)
    rows = db.execute(stmt.order_by(GroupMembership.join_date.asc())).all()
    out = []
    for m, g in rows:
        item = MembershipReadWithGroup.model_validate(m)
        item.group_name = g.name
        item.group_chinese_name = g.chinese_name
        item.group_type = g.group_type
        item.group_uid = g.uid
        root = root_group(db, g)
        if root is not None and root.id != g.id:
            item.root_group_name = root.name
            item.root_group_chinese_name = root.chinese_name
            item.root_group_type = root.group_type
            item.root_group_uid = root.uid
        out.append(item)
    return out


@router.post("", response_model=MembershipRead, status_code=201)
def create_membership(payload: MembershipCreate, db: DbDep):
    require_active(db, Group, payload.group_id, "Group")
    require_active(db, Artist, payload.artist_id, "Artist")
    exists = db.scalar(
        select(GroupMembership).where(
            GroupMembership.group_id == payload.group_id,
            GroupMembership.artist_id == payload.artist_id,
            GroupMembership.status == payload.status,
        )
    )
    if exists:
        raise HTTPException(409, "该成员关系已存在（同组合 + 艺人 + 状态）")
    m = GroupMembership(**payload.model_dump())
    db.add(m)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "成员关系冲突（并发创建或重复），请重试") from e
    db.refresh(m)
    return m


@router.patch("/{membership_id}", response_model=MembershipRead)
def update_membership(membership_id: int, payload: MembershipUpdate, db: DbDep):
    m = db.get(GroupMembership, membership_id)
    if not m:
        raise HTTPException(404, "Membership 不存在")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(m, k, v)
    db.commit()
    db.refresh(m)
    return m


@router.delete("/{membership_id}", status_code=204)
def delete_membership(membership_id: int, db: DbDep):
    m = db.get(GroupMembership, membership_id)
    if not m:
        raise HTTPException(404, "Membership 不存在")
    db.delete(m)
    db.commit()
