"""Group 路由：CRUD + 精简列表 + 成员数。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.api.guards import ensure_name_ci_unique, require_active, validate_release_artist
from app.core.deps import DbDep, PaginationDep
from app.models.artist import Artist
from app.models.company import Company, GroupCompanyRelation
from app.models.group import Group
from app.models.membership import GroupMembership
from app.models.music_video import MusicVideo, music_video_groups
from app.schemas import (
    FetchExternalRequest,
    FetchExternalResult,
    GroupBrief,
    GroupCreate,
    GroupFuzzyHit,
    GroupRead,
    GroupSubUnitBrief,
    GroupUpdate,
    PageResponse,
)
from app.services import audiodb_service, entity_image_service, external_providers, img_variant, portrait_service
from app.services.derived_http import serve_derived_image
from app.services.completion import group_completion_pct
from app.services.soft_delete_service import soft_delete_group
from app.services.name_match import fuzzy_groups, related_artist_ids_for_groups

router = APIRouter(prefix="/groups", tags=["groups"])


@router.get("", response_model=PageResponse[GroupRead])
def list_groups(
    db: DbDep,
    pagination: PaginationDep,
    q: Optional[str] = Query(None),
    group_type: Optional[str] = Query(None),
    sort: Optional[str] = Query(None, description="排序：completion=完成度升序，-completion=降序"),
    filter: str = Query(
        "all",
        pattern="^(all|linked|orphan)$",
        description="数据过滤：all=全部；linked=有视频或有成员；orphan=无视频且无成员（孤儿数据）",
    ),
):
    completion = group_completion_pct()
    # 有关联视频 / 有成员的存在性：视频/成员侧一次预聚合 IN，替代逐行相关 COUNT
    # （与 _song_list 同款根治；组合成员身份不算视频「关联」，但「有成员」算骨架数据）
    video_group_ids = (
        select(music_video_groups.c.group_id)
        .join(MusicVideo, MusicVideo.id == music_video_groups.c.music_video_id)
        .where(
            MusicVideo.deleted_at.is_(None),
            music_video_groups.c.group_id.is_not(None),
        )
    )
    member_group_ids = select(GroupMembership.group_id).where(
        GroupMembership.group_id.is_not(None)
    )
    stmt = select(Group, completion.label("completion_pct")).where(Group.active_filter())
    if filter == "linked":
        stmt = stmt.where(
            or_(Group.id.in_(video_group_ids), Group.id.in_(member_group_ids))
        )
    elif filter == "orphan":
        stmt = stmt.where(
            Group.id.not_in(video_group_ids), Group.id.not_in(member_group_ids)
        )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            Group.name.ilike(like)
            | Group.chinese_name.ilike(like)
            | Group.english_name.ilike(like)
        )
    if group_type:
        stmt = stmt.where(Group.group_type == group_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    if sort == "completion":
        stmt = stmt.order_by(completion.asc())
    elif sort == "-completion":
        stmt = stmt.order_by(completion.desc())
    else:
        stmt = stmt.order_by(Group.sort_name.asc().nulls_last())
    rows = db.execute(
        stmt.offset(pagination.offset).limit(pagination.limit)
    ).all()
    page_ids = [g.id for g, _ in rows]
    member_counts: dict[int, int] = {}
    if page_ids:
        # 一枪预载 company_relations：GroupRead.company_ids 是 ORM property
        # （读 company_relations 关系），不预载会逐组懒加载 N+1
        db.execute(
            select(Group)
            .where(Group.id.in_(page_ids))
            .options(selectinload(Group.company_relations))
        )
        # 批量统计活跃成员数（已软删除艺人不计），替代逐组单查 N+1
        for gid, cnt in db.execute(
            select(GroupMembership.group_id, func.count())
            .join(Artist, GroupMembership.artist_id == Artist.id)
            .where(
                GroupMembership.group_id.in_(page_ids),
                GroupMembership.status == "Active",
                Artist.deleted_at.is_(None),
            )
            .group_by(GroupMembership.group_id)
        ).all():
            member_counts[gid] = cnt
    items = []
    for g, pct in rows:
        read = GroupRead.model_validate(g)
        read.member_count = member_counts.get(g.id, 0)
        read.completion_pct = round(pct)
        items.append(read)
    return PageResponse(
        items=items, total=total, page=pagination.page, page_size=pagination.page_size
    )


def _validate_parent(db, group_id: Optional[int], parent_group_id: Optional[int]) -> None:
    """校验上级组合：需活跃存在、非自身、且不构成环（新 parent 的祖先链不能包含自己）。"""
    if parent_group_id is None:
        return
    if parent_group_id == group_id:
        raise HTTPException(400, "上级组合不能是自身")
    hop = Group.get_active(db, parent_group_id)
    if not hop:
        raise HTTPException(400, "上级组合不存在或已删除")
    while hop is not None:
        if group_id is not None and hop.id == group_id:
            raise HTTPException(400, "不能把组合挂到自身旗下（构成循环）")
        hop = (
            Group.get_active(db, hop.parent_group_id)
            if hop.parent_group_id is not None
            else None
        )


def _fill_parent_fields(db, read: GroupRead, group: Group) -> GroupRead:
    """详情返回：上级名称 + 旗下小分队列表（均排除软删除）。"""
    if group.parent_group_id is not None:
        parent = Group.get_active(db, group.parent_group_id)
        if parent is not None:
            read.parent_name = parent.name
            read.parent_uid = parent.uid
    subs = db.scalars(
        select(Group)
        .where(Group.parent_group_id == group.id, Group.deleted_at.is_(None))
        .order_by(Group.name)
    ).all()
    read.sub_units = [
        GroupSubUnitBrief(
            uid=s.uid,
            deleted_at=s.deleted_at,
            id=s.id,
            name=s.name,
            chinese_name=s.chinese_name,
            group_type=s.group_type,
        )
        for s in subs
    ]
    return read


@router.get("/brief", response_model=list[GroupBrief])
def list_brief(db: DbDep, q: Optional[str] = None):
    stmt = select(Group).where(Group.active_filter())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Group.name.ilike(like) | Group.chinese_name.ilike(like))
    return db.scalars(stmt.order_by(Group.name).limit(50)).all()


@router.get("/fuzzy", response_model=list[GroupFuzzyHit])
def fuzzy_group_names(db: DbDep, q: Optional[str] = Query(None)):
    """按归一化名称模糊匹配组合（忽略空格/大小写，含各语言名与别名）。"""
    hits = fuzzy_groups(db, q or "")
    return [
        GroupFuzzyHit(
            id=g.id,
            name=g.name,
            chinese_name=g.chinese_name,
            english_name=g.english_name,
            korean_name=g.korean_name,
            score=round(score, 4),
        )
        for g, score in hits
    ]


@router.get("/related-artists")
def related_artists(
    db: DbDep,
    ids: str = Query("", description="comma-separated group ids"),
):
    """入库严格匹配用：组合自身 + 小分队 + 母队的成员艺人 id（含历任）。"""
    raw: list[int] = []
    for part in (ids or "").split(","):
        part = part.strip()
        if part.isdigit():
            n = int(part)
            if n > 0:
                raw.append(n)
    return {"artist_ids": sorted(related_artist_ids_for_groups(db, raw))}


@router.get("/by-uid/{uid}", response_model=GroupRead)
def get_group_by_uid(uid: str, db: DbDep):
    """按永久业务 uid 查询；已软删除的实体默认不可见。"""
    group = Group.get_by_uid(db, uid)
    if not group:
        raise HTTPException(404, "Group 不存在")
    return _fill_parent_fields(db, GroupRead.model_validate(group), group)


@router.post("", response_model=GroupRead, status_code=201)
def create_group(payload: GroupCreate, db: DbDep):
    ensure_name_ci_unique(db, Group, payload.name, "组合")
    _validate_parent(db, None, payload.parent_group_id)
    group = Group(**payload.model_dump())
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


@router.get("/{group_id}", response_model=GroupRead)
def get_group(group_id: int, db: DbDep):
    group = Group.get_active(db, group_id)
    if not group:
        raise HTTPException(404, "Group 不存在")
    return _fill_parent_fields(db, GroupRead.model_validate(group), group)


@router.patch("/{group_id}", response_model=GroupRead)
def update_group(group_id: int, payload: GroupUpdate, db: DbDep):
    group = Group.get_active(db, group_id)
    if not group:
        raise HTTPException(404, "Group 不存在")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        ensure_name_ci_unique(db, Group, data["name"], "组合", exclude_id=group.id)
    if "parent_group_id" in data:
        _validate_parent(db, group.id, data["parent_group_id"])
    company_ids = data.pop("company_ids", None)
    for k, v in data.items():
        setattr(group, k, v)
    if company_ids is not None:
        _sync_group_companies(db, group, company_ids)
    db.commit()
    db.refresh(group)
    return group


def _sync_group_companies(db, group: Group, company_ids: list[int]) -> None:
    """把组合所属公司全量同步为 company_ids（先清空再重建，状态 Active）。"""
    company_ids = list(dict.fromkeys(int(c) for c in company_ids))
    if company_ids:
        valid = set(
            db.scalars(
                select(Company.id).where(
                    Company.id.in_(company_ids), Company.deleted_at.is_(None)
                )
            ).all()
        )
        missing = set(company_ids) - valid
        if missing:
            raise HTTPException(400, f"公司不存在或已删除: {sorted(missing)}")
    existing = list(group.company_relations)
    have_active = {r.company_id for r in existing if r.status == "Active"}
    want = set(company_ids)
    for rel in existing:
        if rel.status == "Active" and rel.company_id not in want:
            rel.status = "Former"
    for cid in company_ids:
        if cid not in have_active:
            db.add(GroupCompanyRelation(group_id=group.id, company_id=cid, status="Active"))


@router.delete("/{group_id}", status_code=204)
def delete_group(group_id: int, db: DbDep):
    """软删除组合：写入 deleted_at 墓碑，并静默拆除成员关系 / 公司关系 /
    活跃小分队的 parent_group_id。

    恢复（回收站）只还原实体本身；已拆除的关系不会自动重建，需重新挂接 /
    重新生长成员。
    """
    group = Group.get_active(db, group_id)
    if not group:
        raise HTTPException(404, "Group 不存在")
    soft_delete_group(db, group)
    db.commit()


@router.get("/{group_id}/avatar")
def get_group_avatar(group_id: int, db: DbDep, request: Request, w: Optional[int] = Query(None)):
    """返回本地头像文件（站点获取下载，带 ETag 条件请求协商）；`w` 可选缩放宽度。"""
    group = Group.get_active(db, group_id)
    if not group:
        raise HTTPException(404, "Group 不存在")
    p = audiodb_service.resolve_avatar_path(group.avatar_path)
    if p is None:
        raise HTTPException(404, "暂无头像")
    if w:
        p2 = img_variant.resized_variant(p, w)
        if p2 is not None:
            return serve_derived_image(request, p2, "image/jpeg")
    return serve_derived_image(request, p, audiodb_service.media_type_of(p))


@router.get("/{group_id}/banner")
def get_group_banner(group_id: int, db: DbDep, request: Request, w: Optional[int] = Query(None)):
    """返回手机详情顶栏横幅（带 ETag 条件请求协商）；`w` 同头像接口。"""
    group = Group.get_active(db, group_id)
    if not group:
        raise HTTPException(404, "Group 不存在")
    p = audiodb_service.resolve_avatar_path(group.banner_path)
    if p is None:
        raise HTTPException(404, "暂无横幅")
    if w:
        p2 = img_variant.resized_variant(p, w)
        if p2 is not None:
            return serve_derived_image(request, p2, "image/jpeg")
    return serve_derived_image(request, p, audiodb_service.media_type_of(p))


@router.delete("/{group_id}/banner")
def delete_group_banner(group_id: int, db: DbDep):
    try:
        portrait_service.remove_banner(db, "group", group_id)
    except portrait_service.PortraitError as e:
        raise HTTPException(400, str(e)) from e
    return {"banner_path": None}


@router.post("/{group_id}/fetch-external", response_model=FetchExternalResult)
def fetch_external_group(group_id: int, payload: FetchExternalRequest, db: DbDep):
    """按需把外部条目写入本地：更新简介、下载并绑定头像。

    只处理未软删除的实体；头像下载失败不影响简介写入。
    """
    group = Group.get_active(db, group_id)
    if not group:
        raise HTTPException(404, "Group 不存在")
    # 指针守卫快照：请求开始时的头像指针值
    was_avatar = group.avatar_path
    try:
        detail = external_providers.get_detail(payload.external_id, fallback_kind="group")
    except audiodb_service.ProviderError as e:
        raise HTTPException(400, str(e)) from e

    applied_biography = False
    applied_image = False
    messages: list[str] = []

    if payload.apply_biography and detail.get("biography"):
        group.description = detail["biography"]
        applied_biography = True

    if payload.apply_image:
        gallery = detail.get("gallery") or []
        img_url = (
            payload.image_url
            or detail.get("thumb")
            or (gallery[0] if gallery else None)
            or detail.get("logo")
            or detail.get("fanart")
            or detail.get("wide")
        )
        if img_url:
            try:
                data, ext = audiodb_service.download_image(img_url)
            except audiodb_service.ProviderError as e:
                messages.append(str(e))
            else:
                # 守卫比对：下载耗时较长，期间用户可能已手动更换头像；
                # 必须用全新查询重读当前指针（不能用 db.refresh，会丢掉同请求
                # 里尚未落库的简介改动）
                current = db.scalar(select(Group.avatar_path).where(Group.id == group.id))
                guarded = current != was_avatar
                try:
                    entity_image_service.append_bytes(
                        db, group, "avatar", data, ext, source="site", set_primary=not guarded
                    )
                except entity_image_service.EntityImageError as e:
                    messages.append(str(e))
                else:
                    if guarded:
                        messages.append("检测到头像已被手动修改，站点图已存入候选，未替换当前头像")
                    else:
                        applied_image = True
        else:
            messages.append("外部数据源没有可用头像")


    # Fandom 结构化扩展：成员导入 / 别名合并 / 出道日期填充（勾选「导入扩展信息」时）
    fandom_fields = detail.get("fandom_fields") or {}
    if payload.apply_members and fandom_fields:
        from app.services.fandom_service import apply_fandom_extras
        messages.extend(apply_fandom_extras(db, "groups", group, fandom_fields))
    db.commit()
    db.refresh(group)
    return FetchExternalResult(
        entity=GroupRead.model_validate(group).model_dump(),
        applied_biography=applied_biography,
        applied_image=applied_image,
        message="；".join(messages) or None,
    )
