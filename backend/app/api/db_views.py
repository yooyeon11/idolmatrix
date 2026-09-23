"""新版资料库视图路由（只读）：关系化列表。与旧版 /groups 等页面并行。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.deps import DbDep
from app.schemas.db_views import (
    FieldSuggestRequest,
    FieldSuggestResponse,
    GroupListResponse,
    GroupWorkspaceResponse,
    SourceApplyRequest,
    SourceApplyResponse,
    SourcePreviewRequest,
    SourcePreviewResponse,
    SourceSearchResponse,
    TracklistPreviewRequest,
    TracklistPreviewResponse,
    ArtistWorkspaceResponse,
    AlbumListResponse,
    AlbumWorkspaceResponse,
    ArtistListResponse,
    SongListResponse,
    SongWorkspaceResponse,
)
from app.services.db_views import (
    build_album_rows,
    build_album_workspace,
    build_artist_rows,
    build_artist_workspace,
    build_group_rows,
    build_group_workspace,
    build_song_rows,
    build_song_workspace,
    suggest_artist_field,
    suggest_group_field,
)
from app.services.source_sync import (
    SourceError,
    _normalize_step,
    apply_growth,
    preview_album_tracklist,
    preview_sources,
    search_candidates,
)

router = APIRouter(prefix="/db", tags=["db-views"])


@router.get("/groups", response_model=GroupListResponse)
def get_group_rows(db: DbDep):
    """组合关系化列表：一行看清成员/公司/作品/完整度/问题数。只读。"""
    return build_group_rows(db)


@router.get("/artists", response_model=ArtistListResponse)
def get_artist_rows(db: DbDep):
    """艺人关系化列表：档案概要 + 所属组合 + 作品/完整度/问题数。只读。"""
    return build_artist_rows(db)


@router.get("/albums", response_model=AlbumListResponse)
def get_album_rows(db: DbDep):
    """专辑关系化列表：发行主体 + 曲目/影像计数 + 完整度/问题数。只读。"""
    return build_album_rows(db)


@router.get("/songs", response_model=SongListResponse)
def get_song_rows(db: DbDep):
    """歌曲关系化列表：演唱者 + 专辑/影像计数 + 完整度/问题数。只读。"""
    return build_song_rows(db)


@router.get("/groups/{uid}/workspace", response_model=GroupWorkspaceResponse)
def get_group_workspace(uid: str, db: DbDep):
    """单组合工作台：可编辑基本信息 + 关系 + 问题清单 + 来源。只读，写入走现有 PATCH。"""
    result = build_group_workspace(db, uid)
    if result is None:
        raise HTTPException(404, "Group 不存在")
    return result


def _source_error(e: SourceError):
    raise HTTPException(400, str(e))


@router.get("/groups/{uid}/source/search", response_model=SourceSearchResponse)
def search_source_candidates(uid: str, q: str, db: DbDep):
    """搜索 Fandom / Wikidata 候选条目，供用户挑选来源。"""
    return search_candidates(q)


@router.post("/groups/{uid}/source/preview", response_model=SourcePreviewResponse)
def preview_group_source(uid: str, payload: SourcePreviewRequest, db: DbDep):
    """抓取多个来源，产出字段 diff + 成员/专辑生长提案。只读。"""
    urls = [u for u in (payload.urls or ([payload.url] if payload.url else [])) if u and u.strip()]
    step = (payload.step or "").strip().lower()
    target_keys = {_normalize_step(t) for t in (payload.targets or [])}
    target_keys.discard(None)
    step_key = _normalize_step(step)
    # 专辑/曲目可仅按组合名检索碟志；成员详情可对在籍成员搜 Fandom
    allow_empty = {"member_details", "albums", "tracks"}
    if not urls and step_key not in allow_empty and not (target_keys & allow_empty):
        raise HTTPException(400, "至少提供一个来源链接")
    try:
        return preview_sources(db, uid, urls, step=payload.step, targets=payload.targets)
    except SourceError as e:
        _source_error(e)


@router.post("/groups/{uid}/source/apply", response_model=SourceApplyResponse)
def apply_group_source(uid: str, payload: SourceApplyRequest, db: DbDep):
    """合并勾选的字段 / 成员 / 专辑提案。🔒 锁定字段自动跳过。"""
    try:
        applied = apply_growth(
            db,
            uid,
            payload.fields,
            payload.members,
            payload.albums,
            payload.source_urls,
            subunits=payload.subunits,
            companies=payload.companies,
            step=payload.step,
        )
        return SourceApplyResponse(applied=applied)
    except SourceError as e:
        _source_error(e)


@router.post("/groups/{uid}/source/tracklist-preview", response_model=TracklistPreviewResponse)
def preview_group_tracklist(uid: str, payload: TracklistPreviewRequest, db: DbDep):
    """先选碟再补曲：拉取用户选定外部碟的曲目预览。只读。"""
    try:
        return preview_album_tracklist(db, uid, payload.album_id, payload.external_id)
    except SourceError as e:
        _source_error(e)


@router.get("/artists/{uid}/workspace", response_model=ArtistWorkspaceResponse)
def get_artist_workspace(uid: str, db: DbDep):
    """solo 艺人工作台：档案 + 所属组合 + 作品 + 问题。只读，写入走现有 PATCH。"""
    result = build_artist_workspace(db, uid)
    if result is None:
        raise HTTPException(404, "Artist 不存在")
    return result


@router.get("/albums/{uid}/workspace", response_model=AlbumWorkspaceResponse)
def get_album_workspace(uid: str, db: DbDep):
    """专辑工作台：基本信息 + 发行主体 + 曲目 + 关联影像 + 问题。只读，写入走现有 PATCH。"""
    result = build_album_workspace(db, uid)
    if result is None:
        raise HTTPException(404, "Album 不存在")
    return result


@router.get("/songs/{uid}/workspace", response_model=SongWorkspaceResponse)
def get_song_workspace(uid: str, db: DbDep):
    """歌曲工作台：基本信息 + 演出者关系 + 专辑/影像关联 + Credits + 问题。只读。"""
    result = build_song_workspace(db, uid)
    if result is None:
        raise HTTPException(404, "Song 不存在")
    return result


@router.post("/groups/{uid}/suggest-field", response_model=FieldSuggestResponse)
def suggest_group_field_api(uid: str, payload: FieldSuggestRequest, db: DbDep):
    """单字段搜索：只检索并返回建议值，不写库（组合）。"""
    try:
        result = suggest_group_field(db, uid, payload.field)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result is None:
        raise HTTPException(404, "Group 不存在")
    return result


@router.post("/artists/{uid}/suggest-field", response_model=FieldSuggestResponse)
def suggest_artist_field_api(uid: str, payload: FieldSuggestRequest, db: DbDep):
    """单字段搜索：只检索并返回建议值，不写库（艺人）。"""
    try:
        result = suggest_artist_field(db, uid, payload.field)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result is None:
        raise HTTPException(404, "Artist 不存在")
    return result


@router.post("/artists/{uid}/source/preview", response_model=SourcePreviewResponse)
def preview_artist_sources_api(uid: str, payload: SourcePreviewRequest, db: DbDep):
    """艺人生长预览：Fandom / 维基百科 / 百度百科 / TMDB 字段级提案。只读。"""
    from app.services.artist_growth import preview_artist_sources

    urls = [u for u in (payload.urls or []) if u and u.strip()]
    try:
        return preview_artist_sources(db, uid, urls)
    except SourceError as e:
        raise HTTPException(400, str(e))


@router.post("/artists/{uid}/source/apply")
def apply_artist_sources_api(uid: str, payload: SourceApplyRequest, db: DbDep):
    """合并勾选的艺人字段提案；🔒 锁定字段自动跳过。"""
    from app.schemas.db_views import ArtistApplyResponse
    from app.services.artist_growth import apply_artist_fields

    try:
        applied = apply_artist_fields(db, uid, payload.fields or {}, payload.source_urls or [])
    except SourceError as e:
        raise HTTPException(400, str(e))
    return ArtistApplyResponse(applied=applied)


@router.post("/artists/{uid}/albums/suggest")
def suggest_artist_albums(uid: str, db: DbDep):
    """solo 专辑生长：iTunes / Deezer / MusicBrainz 聚合检索，产出建碟/补全提案。只读。"""
    from app.services.artist_growth import preview_artist_albums

    try:
        return preview_artist_albums(db, uid)
    except SourceError as e:
        raise HTTPException(400, str(e))


@router.post("/artists/{uid}/albums/apply")
def apply_artist_albums_api(uid: str, payload: dict, db: DbDep):
    """应用勾选的 solo 专辑提案（建碟 / 补发行日 / 补封面 / 写曲目）。"""
    from app.services.artist_growth import apply_artist_albums

    try:
        return apply_artist_albums(
            db,
            uid,
            payload.get("albums") or [],
            include_tracks=bool(payload.get("include_tracks", True)),
        )
    except SourceError as e:
        raise HTTPException(400, str(e))
