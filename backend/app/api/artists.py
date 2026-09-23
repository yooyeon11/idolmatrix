"""Artist 路由：CRUD + 精简列表。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select

from app.api.guards import ensure_name_ci_unique
from app.core.deps import DbDep, PaginationDep
from app.models.artist import Artist
from app.models.music_video import MusicVideo, music_video_artists
from app.schemas import (
    ArtistBrief,
    ArtistCreate,
    ArtistFuzzyHit,
    ArtistRead,
    ArtistUpdate,
    FetchExternalRequest,
    FetchExternalResult,
    PageResponse,
)
from app.services import audiodb_service, entity_image_service, external_providers, img_variant, portrait_service
from app.services.derived_http import serve_derived_image
from app.services.completion import artist_completion_pct
from app.services.name_match import fuzzy_artists, match_artist_exact
from app.services.soft_delete_service import soft_delete_artist

router = APIRouter(prefix="/artists", tags=["artists"])


@router.get("", response_model=PageResponse[ArtistRead])
def list_artists(
    db: DbDep,
    pagination: PaginationDep,
    q: Optional[str] = Query(None, description="按名称搜索"),
    sort: Optional[str] = Query(None, description="排序：completion=完成度升序，-completion=降序"),
    only_with_videos: bool = Query(False, description="仅显示有关联视频（未软删除）的艺人"),
    filter: str = Query(
        "all",
        pattern="^(all|linked|orphan)$",
        description="数据过滤：all=全部；linked=有关联视频（未软删除）；orphan=无关联视频",
    ),
):
    completion = artist_completion_pct()
    stmt = select(Artist, completion.label("completion_pct")).where(
        Artist.active_filter()
    )
    # 有关联视频（m2m，排除软删视频；组合成员身份不算「关联」）：视频侧一次预聚合
    # IN，替代逐行相关 COUNT（v3.2.54 根治，与歌曲列表同款）
    video_artist_ids = (
        select(music_video_artists.c.artist_id)
        .join(MusicVideo, MusicVideo.id == music_video_artists.c.music_video_id)
        .where(
            MusicVideo.deleted_at.is_(None),
            music_video_artists.c.artist_id.is_not(None),
        )
    )
    if only_with_videos or filter == "linked":
        stmt = stmt.where(Artist.id.in_(video_artist_ids))
    elif filter == "orphan":
        stmt = stmt.where(Artist.id.not_in(video_artist_ids))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            Artist.name.ilike(like)
            | Artist.chinese_name.ilike(like)
            | Artist.english_name.ilike(like)
            | Artist.stage_name.ilike(like)
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    if sort == "completion":
        stmt = stmt.order_by(completion.asc())
    elif sort == "-completion":
        stmt = stmt.order_by(completion.desc())
    else:
        stmt = stmt.order_by(Artist.sort_name.asc().nulls_last())
    rows = db.execute(
        stmt.offset(pagination.offset).limit(pagination.limit)
    ).all()
    # 批量 video_count：一次 GROUP BY（口径同旧相关子查询：排除软删视频）
    page_ids = [a.id for a, _ in rows]
    vcounts: dict[int, int] = {}
    if page_ids:
        for aid, cnt in db.execute(
            select(music_video_artists.c.artist_id, func.count())
            .join(MusicVideo, MusicVideo.id == music_video_artists.c.music_video_id)
            .where(
                music_video_artists.c.artist_id.in_(page_ids),
                MusicVideo.deleted_at.is_(None),
            )
            .group_by(music_video_artists.c.artist_id)
        ).all():
            vcounts[aid] = cnt
    items = []
    for artist, pct in rows:
        read = ArtistRead.model_validate(artist)
        read.completion_pct = round(pct)
        read.video_count = vcounts.get(artist.id, 0)
        items.append(read)
    return PageResponse(
        items=items, total=total, page=pagination.page, page_size=pagination.page_size
    )


@router.get("/brief", response_model=list[ArtistBrief])
def list_brief(db: DbDep, q: Optional[str] = None):
    stmt = select(Artist).where(Artist.active_filter())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            Artist.name.ilike(like)
            | Artist.chinese_name.ilike(like)
            | Artist.english_name.ilike(like)
            | Artist.korean_name.ilike(like)
            | Artist.stage_name.ilike(like)
        )
    items = db.scalars(stmt.order_by(Artist.name).limit(50)).all()
    return items


@router.get("/fuzzy", response_model=list[ArtistFuzzyHit])
def fuzzy_artist_names(db: DbDep, q: Optional[str] = Query(None)):
    """按归一化人名模糊匹配艺人（忽略空格/连字符/大小写，含别名）。"""
    hits = fuzzy_artists(db, q or "")
    return [
        ArtistFuzzyHit(
            id=a.id,
            name=a.name,
            chinese_name=a.chinese_name,
            stage_name=a.stage_name,
            korean_name=a.korean_name,
            score=round(s, 4),
        )
        for a, s in hits
    ]


@router.get("/by-uid/{uid}", response_model=ArtistRead)
def get_artist_by_uid(uid: str, db: DbDep):
    """按永久业务 uid 查询；已软删除的实体默认不可见。"""
    artist = Artist.get_by_uid(db, uid)
    if not artist:
        raise HTTPException(404, "Artist 不存在")
    return artist


@router.post("", response_model=ArtistRead, status_code=201)
def create_artist(payload: ArtistCreate, db: DbDep):
    if not payload.allow_name_conflict:
        ensure_name_ci_unique(db, Artist, payload.name, "艺人")
        twin = match_artist_exact(
            db,
            payload.name,
            payload.stage_name,
            payload.korean_name,
            payload.chinese_name,
            extra=payload.aliases,
        )
        if twin is not None:
            raise HTTPException(
                409,
                f"已有艺人「{twin.name}」与该名称过于接近（空格/大小写/别名视为同一人）",
            )
    data = {k: v for k, v in payload.model_dump().items() if k != "allow_name_conflict"}
    # 艺名留空时默认取主名，方便建立时直接带出（可在编辑面板修改）
    if not data.get("stage_name"):
        data["stage_name"] = data.get("name")
    artist = Artist(**data)
    db.add(artist)
    db.commit()
    db.refresh(artist)
    return artist


@router.get("/{artist_id}", response_model=ArtistRead)
def get_artist(artist_id: int, db: DbDep):
    artist = Artist.get_active(db, artist_id)
    if not artist:
        raise HTTPException(404, "Artist 不存在")
    return artist


@router.patch("/{artist_id}", response_model=ArtistRead)
def update_artist(artist_id: int, payload: ArtistUpdate, db: DbDep):
    artist = Artist.get_active(db, artist_id)
    if not artist:
        raise HTTPException(404, "Artist 不存在")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        ensure_name_ci_unique(db, Artist, data["name"], "艺人", exclude_id=artist.id)
    for k, v in data.items():
        setattr(artist, k, v)
    db.commit()
    db.refresh(artist)
    return artist


@router.delete("/{artist_id}", status_code=204)
def delete_artist(artist_id: int, db: DbDep):
    """软删除艺人：写入 deleted_at 墓碑，并静默拆除成员关系 / 公司关系 /
    MV 直拍主体指针。

    恢复（回收站）只还原实体本身；已拆除的关系不会自动重建，需重新挂接。
    """
    artist = Artist.get_active(db, artist_id)
    if not artist:
        raise HTTPException(404, "Artist 不存在")
    soft_delete_artist(db, artist)
    db.commit()


@router.get("/{artist_id}/avatar")
def get_artist_avatar(artist_id: int, db: DbDep, request: Request, w: Optional[int] = Query(None)):
    """返回本地头像文件（站点获取下载，带 ETag 条件请求协商）。

    `w` 可选缩放宽度：小尺寸展示位（首页头像胶囊等）用它降低传输体积，
    外网慢速链路下减少加载失败；无缩放需求不传即出原图。
    """
    artist = Artist.get_active(db, artist_id)
    if not artist:
        raise HTTPException(404, "Artist 不存在")
    p = audiodb_service.resolve_avatar_path(artist.avatar_path)
    if p is None:
        raise HTTPException(404, "暂无头像")
    if w:
        p2 = img_variant.resized_variant(p, w)
        if p2 is not None:
            return serve_derived_image(request, p2, "image/jpeg")
    return serve_derived_image(request, p, audiodb_service.media_type_of(p))


@router.get("/{artist_id}/banner")
def get_artist_banner(artist_id: int, db: DbDep, request: Request, w: Optional[int] = Query(None)):
    """返回手机详情顶栏横幅（带 ETag 条件请求协商）；`w` 同头像接口。"""
    artist = Artist.get_active(db, artist_id)
    if not artist:
        raise HTTPException(404, "Artist 不存在")
    p = audiodb_service.resolve_avatar_path(artist.banner_path)
    if p is None:
        raise HTTPException(404, "暂无横幅")
    if w:
        p2 = img_variant.resized_variant(p, w)
        if p2 is not None:
            return serve_derived_image(request, p2, "image/jpeg")
    return serve_derived_image(request, p, audiodb_service.media_type_of(p))


@router.delete("/{artist_id}/banner")
def delete_artist_banner(artist_id: int, db: DbDep):
    try:
        portrait_service.remove_banner(db, "artist", artist_id)
    except portrait_service.PortraitError as e:
        raise HTTPException(400, str(e)) from e
    return {"banner_path": None}


@router.post("/{artist_id}/fetch-external", response_model=FetchExternalResult)
def fetch_external_artist(artist_id: int, payload: FetchExternalRequest, db: DbDep):
    """按需把外部条目写入本地：更新简介、下载并绑定头像。

    只处理未软删除的实体；头像下载失败不影响简介写入。
    """
    artist = Artist.get_active(db, artist_id)
    if not artist:
        raise HTTPException(404, "Artist 不存在")
    # 指针守卫快照：请求开始时的头像指针值
    was_avatar = artist.avatar_path
    try:
        detail = external_providers.get_detail(payload.external_id, fallback_kind="artist")
    except audiodb_service.ProviderError as e:
        raise HTTPException(400, str(e)) from e

    applied_biography = False
    applied_image = False
    messages: list[str] = []

    if payload.apply_biography and detail.get("biography"):
        artist.description = detail["biography"]
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
                current = db.scalar(select(Artist.avatar_path).where(Artist.id == artist.id))
                guarded = current != was_avatar
                try:
                    entity_image_service.append_bytes(
                        db, artist, "avatar", data, ext, source="site", set_primary=not guarded
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

    # Fandom 结构化扩展：别名合并 / 出道日期填充（勾选「导入扩展信息」时）
    fandom_fields = detail.get("fandom_fields") or {}
    if payload.apply_members and fandom_fields:
        from app.services.fandom_service import apply_fandom_extras
        messages.extend(apply_fandom_extras(db, "artists", artist, fandom_fields))

    db.commit()
    db.refresh(artist)
    return FetchExternalResult(
        entity=ArtistRead.model_validate(artist).model_dump(),
        applied_biography=applied_biography,
        applied_image=applied_image,
        message="；".join(messages) or None,
    )
