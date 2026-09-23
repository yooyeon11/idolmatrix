"""MusicVideo 路由：CRUD + 系统播放器打开 + 封面。

播放相关能力（Direct Play / Direct Stream / Transcode）已统一收敛到
app.api.playback（PlaybackSession），本模块不再提供任何播放源端点。
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from app.api.guards import require_active
from app.core.config import settings
from app.core.deps import DbDep, PaginationDep
from app.services.soft_delete_service import soft_delete_music_video
from app.media.ffmpeg import FFmpegError
from app.models.album import AlbumTrack
from app.models.artist import Artist
from app.models.base import resolve_active_ids
from app.models.group import Group
from app.models.membership import GroupMembership
from app.models.music_video import MusicVideo, MusicVideoTrack
from app.models.song import Song, SongArtistRelation
from app.schemas import (
    AlbumBrief,
    SongBrief,
    CoverFrameApplyRequest,
    CoverFrameItem,
    CoverFramesRequest,
    CoverFramesResponse,
    FocusResponse,
    FolderBrowseResult,
    FolderChild,
    MessageResponse,
    MusicVideoBrief,
    MusicVideoCreate,
    MusicVideoRead,
    MusicVideoTrackRead,
    MusicVideoUpdate,
    PageResponse,
    ResolutionFacet,
    ResolutionFacetResponse,
    VideoPathSuggestion,
    VideoRelocateRequest,
    VideoRelocateResult,
)
from app.services.derived_http import serve_derived_image
from app.services.file_service import COVER_EXTENSIONS, find_sidecar_cover
from app.services.img_variant import VIDEO_THUMB_CACHE_DIRNAME, resized_variant
from app.services.library_service import (
    ArtistDirNameConflict,
    aggregate_folder_browse,
    maybe_cleanup_missing_videos,
    relocate_video_file,
    resolve_video_abs_path,
    suggest_relocate_path,
)
from app.services.video_thumb import (
    apply_manual_cover,
    clear_manual_cover,
    cover_frame_path,
    ensure_video_thumbnail,
    generate_cover_frames,
)
from app.services.relation_service import apply_video_song_tracks
from app.services.group_tree import expand_group_ids
from app.services.video_meta import (
    RESOLUTION_TIERS,
    RESOLUTION_UNKNOWN,
    cover_from_names,
    is_short_video,
    normalize_video_types,
    resolution_ranges,
    resolution_tier,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/music-videos", tags=["music-videos"])

# ===== 封面变体（列表位按展示宽度出小图）=====
# 抽帧默认按视频原始分辨率落盘，列表只展示 180~320 CSS px，
# 前端按展示宽度传 w，这里按需缩放并缓存到独立目录（与实体图片隔离，便于纳管清理）。
COVER_VARIANT_QUALITY = 85
# 与 library_service._purge_derived_for 的清理逻辑共用同一字面量，避免改名后漏清
COVER_VARIANT_CACHE_DIR = VIDEO_THUMB_CACHE_DIRNAME
# 封面接口沿用「仅私有缓存」语义：登录门禁下的资源不应落进共享缓存
COVER_CACHE_CONTROL = "private, max-age=0, must-revalidate"


def _relations_options():
    """统一的关联预加载选项，避免 N+1。"""
    return (
        joinedload(MusicVideo.song),
        joinedload(MusicVideo.subject_artist),
        selectinload(MusicVideo.songs).selectinload(Song.album_tracks).joinedload(AlbumTrack.album),
        selectinload(MusicVideo.albums),
        selectinload(MusicVideo.artists),
        selectinload(MusicVideo.groups),
        selectinload(MusicVideo.video_tracks).selectinload(MusicVideoTrack.albums),
        selectinload(MusicVideo.video_tracks).joinedload(MusicVideoTrack.song)
        .selectinload(Song.artist_relations)
        .joinedload(SongArtistRelation.artist),
        selectinload(MusicVideo.video_tracks).joinedload(MusicVideoTrack.song)
        .selectinload(Song.artist_relations)
        .joinedload(SongArtistRelation.group),
    )

def _get_active(db, mv_id: int) -> MusicVideo:
    """查询未软删除的 MusicVideo；不存在或已删除返回 None。"""
    return db.scalar(
        select(MusicVideo)
        .options(*_relations_options())
        .where(MusicVideo.id == mv_id, MusicVideo.deleted_at.is_(None))
    )

def _resolve_ids(db, model, ids):
    """把 id 列表解析为未软删除 ORM 对象列表（过滤无效 / 已删除 id）。"""
    return resolve_active_ids(db, model, ids)

def _split_relations(data: dict) -> tuple[dict, dict]:
    """从 payload dict 中拆出多对多 id 字段，返回 (标量字段, 关联字段)。"""
    rel = {}
    for key in ("song_ids", "album_ids", "artist_ids", "group_ids", "tracks"):
        if key in data:
            rel[key] = data.pop(key)
    return data, rel

def _apply_relations(mv: MusicVideo, db, rel: dict) -> None:
    """把多对多 id 字段应用到 ORM 关系上。专辑由曲目行派生，不再与歌曲打笛卡尔积。"""
    if rel.get("tracks") is not None:
        apply_video_song_tracks(db, mv, tracks=rel["tracks"])
    elif "song_ids" in rel:
        apply_video_song_tracks(
            db, mv, song_ids=rel["song_ids"], album_ids=rel.get("album_ids")
        )
    elif "album_ids" in rel:
        apply_video_song_tracks(
            db,
            mv,
            song_ids=[s.id for s in mv.songs],
            album_ids=rel["album_ids"],
        )
    if "artist_ids" in rel:
        mv.artists = _resolve_ids(db, Artist, rel["artist_ids"])
    if "group_ids" in rel:
        mv.groups = _resolve_ids(db, Group, rel["group_ids"])

def _sync_primary_type(mv: MusicVideo) -> None:
    """video_types 为来源；video_type 永远同步成第一项，供索引/统计。"""
    primary, types = normalize_video_types(mv.video_types, mv.video_type)
    mv.video_type = primary
    mv.video_types = types

def _resolve_file_path(mv: MusicVideo) -> Path:
    """根据 ingestion_status 解析实际文件绝对路径（兼容 Windows 风格反斜杠）。"""
    p = resolve_video_abs_path(mv)
    if p is None:
        raise HTTPException(404, "视频文件路径缺失")
    return p

def _fill_relations(mv: MusicVideo) -> None:
    """把已 joinedload 的 song/subject_artist 名称回填到 ORM 对象上，
    便于 Pydantic from_attributes 直接序列化为 MusicVideoRead/Brief 的关联字段。

    要求调用方已用 joinedload 预加载 song / subject_artist，否则会触发 N+1。
    已软删除的关联实体按不存在处理，不回填名称。
    """
    # 用 __dict__ 直接挂属性，绕过 SQLAlchemy 的 instrumented attribute 限制
    primary_song = None
    vtracks = list(getattr(mv, "video_tracks", None) or [])
    if vtracks:
        vtracks.sort(key=lambda r: r.position)
        primary_song = getattr(vtracks[0], "song", None)
        if primary_song is not None and getattr(primary_song, "deleted_at", None) is not None:
            primary_song = None
    if primary_song is None and mv.song is not None and not mv.song.is_deleted:
        primary_song = mv.song
    if primary_song is not None:
        mv.__dict__['song_name'] = primary_song.name
        mv.__dict__['song_chinese_name'] = primary_song.chinese_name
    else:
        mv.__dict__.setdefault('song_name', None)
        mv.__dict__.setdefault('song_chinese_name', None)
    if mv.subject_artist is not None and not mv.subject_artist.is_deleted:
        mv.__dict__['subject_artist_name'] = mv.subject_artist.name
        mv.__dict__['subject_artist_chinese_name'] = mv.subject_artist.chinese_name
    else:
        mv.__dict__.setdefault('subject_artist_name', None)
        mv.__dict__.setdefault('subject_artist_chinese_name', None)

def _albums_from_video_tracks(mv: MusicVideo) -> list:
    """展示用：本视频曲目行上选中的专辑去重。"""
    seen: set[int] = set()
    albums = []
    for row in getattr(mv, "video_tracks", None) or []:
        for album in getattr(row, "albums", None) or []:
            if getattr(album, "deleted_at", None) is not None or album.id in seen:
                continue
            seen.add(album.id)
            albums.append(album)
    return albums


def _to_read(mv: MusicVideo) -> MusicVideoRead:
    """序列化：曲目表是歌曲/专辑的唯一来源；video_types 是类型的唯一来源。"""
    read = MusicVideoRead.model_validate(mv)
    primary, types = normalize_video_types(read.video_types, read.video_type)
    read.video_type = primary
    read.video_types = types
    derived = _albums_from_video_tracks(mv)
    read.albums = [
        AlbumBrief.model_validate(a) for a in derived if a.deleted_at is None
    ]
    read.artists = [a for a in read.artists if a.deleted_at is None]
    read.groups = [g for g in read.groups if g.deleted_at is None]
    track_rows: list[MusicVideoTrackRead] = []
    song_briefs = []
    seen_songs: set[int] = set()
    vtracks = list(getattr(mv, "video_tracks", None) or [])
    for row in sorted(vtracks, key=lambda r: r.position):
        song = getattr(row, "song", None)
        if song is not None and getattr(song, "deleted_at", None) is not None:
            continue
        aids = [
            a.id
            for a in (row.albums or [])
            if getattr(a, "deleted_at", None) is None
        ]
        track_rows.append(MusicVideoTrackRead(song_id=row.song_id, album_ids=aids))
        if song is not None and song.id not in seen_songs:
            seen_songs.add(song.id)
            song_briefs.append(SongBrief.model_validate(song))
    read.tracks = track_rows
    read.songs = song_briefs
    read.song_ids = [t.song_id for t in track_rows]
    read.song_id = track_rows[0].song_id if track_rows else None
    read.cover_from = cover_from_names(mv)
    return read

def _build_sort_order(sort: Optional[str]):
    """构建列表排序；日期排序时无日期的记录恒排最后。"""
    if sort == "performance_date_desc":
        return (MusicVideo.performance_date.is_(None), MusicVideo.performance_date.desc())
    if sort == "performance_date_asc":
        return (MusicVideo.performance_date.is_(None), MusicVideo.performance_date.asc())
    if sort == "published_date_desc":
        return (MusicVideo.published_date.is_(None), MusicVideo.published_date.desc())
    if sort == "published_date_asc":
        return (MusicVideo.published_date.is_(None), MusicVideo.published_date.asc())
    return (MusicVideo.created_at.desc(),)


@router.get("", response_model=PageResponse[MusicVideoRead])
def list_videos(
    db: DbDep,
    pagination: PaginationDep,
    q: Optional[str] = Query(None),
    video_type: Optional[str] = Query(None),
    video_types: Optional[str] = Query(None, description="逗号分隔的多视频类型过滤"),
    song_id: Optional[int] = Query(None),
    subject_artist_id: Optional[int] = Query(None),
    artist_id: Optional[int] = Query(None),
    group_id: Optional[int] = Query(None),
    ingestion_status: Optional[str] = Query(None),
    uploader: Optional[str] = Query(None, description="按上传人（频道）过滤"),
    is_short: Optional[bool] = Query(
        None, description="true=仅短视频，false=排除短视频，缺省不过滤"
    ),
    resolution: Optional[str] = Query(
        None,
        description="逗号分隔的分辨率档位过滤：8k/4k/2k/1080p/720p/480p/360p/240p/sd/unknown",
    ),
    sort: Optional[str] = Query(
        None,
        description="排序：performance_date_desc/asc、published_date_desc/asc，默认按入库时间倒序",
    ),
):
    maybe_cleanup_missing_videos(db)
    stmt = (
        select(MusicVideo)
        .options(*_relations_options())
        .where(MusicVideo.deleted_at.is_(None))
    )
    stmt = _filter_by_query_and_types(stmt, q, video_type, video_types)
    if song_id:
        stmt = stmt.where(
            or_(
                MusicVideo.song_id == song_id,
                MusicVideo.video_tracks.any(MusicVideoTrack.song_id == song_id),
                MusicVideo.songs.any(Song.id == song_id),
            ),
            # 翻唱不跟随原唱歌曲：A 翻唱 B 的歌只留在 A 的主页，不出现在歌曲页
            ~_is_cover_clause(),
        )
    if subject_artist_id:
        stmt = stmt.where(MusicVideo.subject_artist_id == subject_artist_id)
    if artist_id:
        stmt = stmt.where(
            MusicVideo.artists.any(Artist.id == artist_id),
            # 翻唱只跟随表演主体：subject 命中本艺人才显示；subject 未定（历史数据）
            # 时外层 artist 关联本身就是过滤条件，放行保持老行为
            or_(
                ~_is_cover_clause(),
                MusicVideo.subject_artist_id == artist_id,
                MusicVideo.subject_artist_id.is_(None),
            ),
        )
    if group_id:
        # 完整体聚合：连同旗下小分队（后代）一起命中
        family_ids = expand_group_ids(db, group_id)
        stmt = stmt.where(
            MusicVideo.groups.any(Group.id.in_(family_ids)),
            # 翻唱只跟随表演主体所在组合：subject 艺人的成员关系命中家族才显示；
            # subject 未定时无法区分表演方/原唱方（组合翻唱常只有组合关联、无艺人关联），
            # 放行保持老行为 —— 只拦「subject 明确指向他人」的误挂数据
            or_(
                ~_is_cover_clause(),
                MusicVideo.subject_artist.has(
                    Artist.memberships.any(GroupMembership.group_id.in_(family_ids))
                ),
                MusicVideo.subject_artist_id.is_(None),
            ),
        )
    if ingestion_status:
        stmt = stmt.where(MusicVideo.ingestion_status == ingestion_status)
    if uploader:
        stmt = stmt.where(MusicVideo.original_uploader == uploader)
    stmt = _filter_is_short(stmt, is_short)
    stmt = _filter_resolution(stmt, resolution)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = (
        db.scalars(
            stmt.order_by(*_build_sort_order(sort))
            .offset(pagination.offset)
            .limit(pagination.limit)
        )
        .all()
    )
    for mv in items:
        _fill_relations(mv)
    return PageResponse(
        items=[_to_read(mv) for mv in items],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )

@router.get("/brief", response_model=list[MusicVideoBrief])
def list_brief(
    db: DbDep,
    video_type: Optional[str] = None,
    ingestion_status: Optional[str] = None,
    limit: int = Query(50, le=200),
):
    maybe_cleanup_missing_videos(db)
    stmt = (
        select(MusicVideo)
        .options(*_relations_options())
        .where(MusicVideo.deleted_at.is_(None))
    )
    if video_type:
        stmt = stmt.where(MusicVideo.video_type == video_type)
    if ingestion_status:
        stmt = stmt.where(MusicVideo.ingestion_status == ingestion_status)
    items = db.scalars(stmt.order_by(MusicVideo.created_at.desc()).limit(limit)).all()
    for mv in items:
        _fill_relations(mv)
    return items


def _ilike_any(model, like: str, *fields: str):
    return or_(*(getattr(model, f).ilike(like) for f in fields))


def _is_cover_clause():
    """翻唱判定：主类型或 video_types 数组含 CoverStage（与前端 videoHasType 同口径）。"""
    return or_(
        MusicVideo.video_type == "CoverStage",
        cast(MusicVideo.video_types, String).ilike('%"CoverStage"%'),
    )


def _filter_by_query_and_types(stmt, q: Optional[str], video_type: Optional[str], video_types: Optional[str]):
    if q and q.strip():
        like = f"%{q.strip()}%"
        song_hit = and_(
            Song.active_filter(),
            _ilike_any(Song, like, "name", "chinese_name", "english_name", "korean_name"),
        )
        artist_hit = and_(
            Artist.active_filter(),
            _ilike_any(
                Artist, like, "name", "chinese_name", "english_name", "korean_name", "stage_name"
            ),
        )
        group_hit = and_(
            Group.active_filter(),
            _ilike_any(Group, like, "name", "chinese_name", "english_name", "korean_name"),
        )
        stmt = stmt.where(
            or_(
                MusicVideo.name.ilike(like),
                MusicVideo.original_title.ilike(like),
                MusicVideo.chinese_name.ilike(like),
                MusicVideo.english_name.ilike(like),
                MusicVideo.korean_name.ilike(like),
                MusicVideo.source_id.ilike(like),
                MusicVideo.video_tracks.any(MusicVideoTrack.song.has(song_hit)),
                MusicVideo.songs.any(song_hit),
                MusicVideo.song.has(song_hit),
                MusicVideo.artists.any(artist_hit),
                MusicVideo.subject_artist.has(artist_hit),
                MusicVideo.groups.any(group_hit),
            )
        )
    if video_type:
        stmt = stmt.where(MusicVideo.video_type == video_type)
    if video_types:
        types = [t.strip() for t in video_types.split(",") if t.strip()]
        if types:
            type_clauses = [MusicVideo.video_type.in_(types)]
            for t in types:
                type_clauses.append(
                    cast(MusicVideo.video_types, String).ilike(f'%"{t}"%')
                )
            stmt = stmt.where(or_(*type_clauses))
    return stmt


def _filter_is_short(stmt, is_short: Optional[bool]):
    if is_short is True:
        return stmt.where(MusicVideo.is_short.is_(True))
    if is_short is False:
        return stmt.where(
            or_(MusicVideo.is_short.is_(False), MusicVideo.is_short.is_(None))
        )
    return stmt


def _filter_resolution(stmt, resolution: Optional[str]):
    """按分辨率档位过滤（逗号分隔多选），档位口径与统计页一致：按短边 min(width, height) 归档。

    不依赖方言的 least()/min()：短边 >= lo ⟺ 宽高都 >= lo；短边 < hi ⟺ 宽高任一 < hi。
    缺宽高的记录只命中 unknown 档，与 resolution_tier() 的归档结果一致。
    """
    if not resolution:
        return stmt
    keys = [k.strip().lower() for k in resolution.split(",") if k.strip()]
    if not keys:
        return stmt
    ranges = {key: (lo, hi) for key, lo, hi in resolution_ranges()}
    clauses = []
    for key in keys:
        if key == RESOLUTION_UNKNOWN:
            clauses.append(
                or_(
                    MusicVideo.width.is_(None),
                    MusicVideo.height.is_(None),
                    MusicVideo.width <= 0,
                    MusicVideo.height <= 0,
                )
            )
            continue
        rng = ranges.get(key)
        if rng is None:
            continue
        lo, hi = rng
        conds = [MusicVideo.width >= lo, MusicVideo.height >= lo]
        if hi is not None:
            conds.append(or_(MusicVideo.width < hi, MusicVideo.height < hi))
        clauses.append(and_(*conds))
    if not clauses:
        return stmt
    return stmt.where(or_(*clauses))


@router.get("/folder-browse", response_model=FolderBrowseResult)
def folder_browse(
    db: DbDep,
    pagination: PaginationDep,
    prefix: str = Query("", description="当前目录，相对正式库，POSIX"),
    q: Optional[str] = Query(None),
    video_type: Optional[str] = Query(None),
    video_types: Optional[str] = Query(None),
    is_short: Optional[bool] = Query(None, description="true=仅短视频，false=排除短视频"),
    resolution: Optional[str] = Query(
        None, description="逗号分隔的分辨率档位过滤，同列表接口"
    ),
    sort: Optional[str] = Query(None, description="排序，同列表接口"),
):
    """文件夹浏览：子文件夹从全部库记录聚合，当前目录视频分页。"""
    maybe_cleanup_missing_videos(db)
    stmt = select(MusicVideo.id, MusicVideo.file_path).where(
        MusicVideo.deleted_at.is_(None),
        MusicVideo.ingestion_status == "library",
    )
    stmt = _filter_by_query_and_types(stmt, q, video_type, video_types)
    stmt = _filter_is_short(stmt, is_short)
    stmt = _filter_resolution(stmt, resolution)
    rows = db.execute(stmt.order_by(*_build_sort_order(sort))).all()
    folders, video_ids = aggregate_folder_browse(rows, prefix)
    video_total = len(video_ids)
    page_ids = video_ids[pagination.offset : pagination.offset + pagination.limit]
    videos: list[MusicVideoRead] = []
    if page_ids:
        loaded = db.scalars(
            select(MusicVideo)
            .options(*_relations_options())
            .where(MusicVideo.id.in_(page_ids))
        ).all()
        by_id = {mv.id: mv for mv in loaded}
        for vid in page_ids:
            mv = by_id.get(vid)
            if mv is None:
                continue
            _fill_relations(mv)
            videos.append(_to_read(mv))
    return FolderBrowseResult(
        prefix=(prefix or "").replace("\\", "/").strip("/"),
        folders=[FolderChild(**f) for f in folders],
        videos=videos,
        video_total=video_total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/resolution-facets", response_model=ResolutionFacetResponse)
def resolution_facets(
    db: DbDep,
    q: Optional[str] = Query(None),
    video_type: Optional[str] = Query(None),
    video_types: Optional[str] = Query(None),
    is_short: Optional[bool] = Query(None),
    ingestion_status: Optional[str] = Query(None),
):
    """当前筛选条件下的分辨率档位计数（浏览页「分辨率」筛选菜单：空档位不展示）。

    档位归档与统计页同一套口径（video_meta.resolution_tier，按短边），
    所以菜单里的条数与选中后列表的实际条数一致。
    """
    stmt = select(
        MusicVideo.width, MusicVideo.height, func.count(MusicVideo.id)
    ).where(MusicVideo.deleted_at.is_(None))
    stmt = _filter_by_query_and_types(stmt, q, video_type, video_types)
    if ingestion_status:
        stmt = stmt.where(MusicVideo.ingestion_status == ingestion_status)
    stmt = _filter_is_short(stmt, is_short)
    counts: dict[str, int] = {}
    rows = db.execute(stmt.group_by(MusicVideo.width, MusicVideo.height)).all()
    for width, height, cnt in rows:
        key = resolution_tier(width, height)
        counts[key] = counts.get(key, 0) + int(cnt)
    ordered = [key for key, _floor in RESOLUTION_TIERS if key in counts]
    if RESOLUTION_UNKNOWN in counts:
        ordered.append(RESOLUTION_UNKNOWN)
    return ResolutionFacetResponse(
        items=[ResolutionFacet(key=key, count=counts[key]) for key in ordered]
    )


@router.get("/by-uid/{uid}", response_model=MusicVideoRead)
def get_video_by_uid(uid: str, db: DbDep):
    """按永久业务 uid 查询；已软删除的实体默认不可见。"""
    mv = db.scalar(
        select(MusicVideo)
        .options(*_relations_options())
        .where(MusicVideo.uid == uid, MusicVideo.deleted_at.is_(None))
    )
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    _fill_relations(mv)
    return _to_read(mv)

@router.post("", response_model=MusicVideoRead, status_code=201)
def create_video(payload: MusicVideoCreate, db: DbDep):
    data = payload.model_dump()
    data, rel = _split_relations(data)
    if rel.get("tracks") is not None:
        data.pop("song_id", None)
        data.pop("song_ids", None)
        data.pop("album_ids", None)
    primary, types = normalize_video_types(data.get("video_types"), data.get("video_type"))
    data["video_type"] = primary
    data["video_types"] = types
    if data.get("song_id") is not None:
        require_active(db, Song, data["song_id"], "Song")
    if data.get("subject_artist_id") is not None:
        require_active(db, Artist, data["subject_artist_id"], "Artist(subject)")
    mv = MusicVideo(**data)
    db.add(mv)
    try:
        db.flush()
        _apply_relations(mv, db, rel)
        _sync_primary_type(mv)
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            409,
            "已存在 file_hash 或 source_platform+source_id 相同的活跃视频",
        ) from e
    db.refresh(mv)
    # 重新加载关联，便于返回时填充名称
    mv = db.scalar(
        select(MusicVideo)
        .options(*_relations_options())
        .where(MusicVideo.id == mv.id)
    )
    _fill_relations(mv)
    return _to_read(mv)

@router.get("/{mv_id}", response_model=MusicVideoRead)
def get_video(mv_id: int, db: DbDep):
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    _fill_relations(mv)
    return _to_read(mv)

@router.patch("/{mv_id}", response_model=MusicVideoRead)
def update_video(mv_id: int, payload: MusicVideoUpdate, db: DbDep):
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    data = payload.model_dump(exclude_unset=True)
    data, rel = _split_relations(data)
    if rel.get("tracks") is not None:
        data.pop("song_id", None)
        data.pop("song_ids", None)
        data.pop("album_ids", None)
    if "video_types" in data or "video_type" in data:
        primary, types = normalize_video_types(
            data.get("video_types", mv.video_types),
            data.get("video_type", mv.video_type),
        )
        data["video_type"] = primary
        data["video_types"] = types
    # is_short 恒由系统自动派生（入库/编辑都同口径）：**只看视频类型是否含 ShortVideo，与时长无关**。
    # 前端不再手动标记 is_short，故 update 未显式携带时按最新类型重算。
    if "is_short" not in data:
        data["is_short"] = is_short_video(
            data.get("video_types", mv.video_types),
            data.get("video_type", mv.video_type),
        )
    if data.get("song_id") is not None:
        require_active(db, Song, data["song_id"], "Song")
    if data.get("subject_artist_id") is not None:
        require_active(db, Artist, data["subject_artist_id"], "Artist(subject)")
    for k, v in data.items():
        setattr(mv, k, v)
    _apply_relations(mv, db, rel)
    _sync_primary_type(mv)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            409,
            "已存在 file_hash 或 source_platform+source_id 相同的活跃视频",
        ) from e
    # 重新加载关联，便于返回时填充名称
    mv = _get_active(db, mv_id)
    _fill_relations(mv)
    return _to_read(mv)

@router.get("/{mv_id}/path-suggestion", response_model=VideoPathSuggestion)
def get_video_path_suggestion(mv_id: int, db: DbDep):
    """已入库视频的存储路径建议：按当前元数据走自动整理规则推导目标路径。"""
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    current = (mv.file_path or "").replace("\\", "/").strip()
    suggested_rel: Optional[Path] = None
    abs_path: Optional[Path] = None
    notice: Optional[str] = None
    if mv.ingestion_status == "library" and current:
        try:
            suggested_rel = suggest_relocate_path(mv, db=db)
        except ArtistDirNameConflict as exc:
            # 重名艺人消歧受阻（缺中文名）：不给推荐路径，但把原因带出去
            suggested_rel = None
            notice = str(exc)
        if suggested_rel is not None:
            abs_path = (settings.library_dir / suggested_rel).resolve()
    return VideoPathSuggestion(
        current_path=current,
        suggested_path=suggested_rel.as_posix() if suggested_rel else None,
        absolute_path=abs_path.as_posix() if abs_path else None,
        notice=notice,
    )

@router.post("/{mv_id}/relocate", response_model=VideoRelocateResult)
def relocate_video_endpoint(mv_id: int, payload: VideoRelocateRequest, db: DbDep):
    """更改已入库视频的存储路径并移动文件；destination_rel 为空时按规则自动推导。"""
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    try:
        return relocate_video_file(db, mv, destination_rel=payload.destination_rel)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))

@router.delete("/{mv_id}", status_code=204)
def delete_video(mv_id: int, db: DbDep):
    """软删除影像：写入 deleted_at 墓碑，保留 id/uid/元数据/文件关联。"""
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    soft_delete_music_video(db, mv)
    db.commit()

def _find_sibling_cover(mv: MusicVideo, db) -> Optional[Path]:
    """在视频同名文件旁查找本地封面，找到后登记为缩略图并持久化。

    兼容入库时封面尚未就绪、事后补放的场景（如 a.mkv 旁出现 a.webp）。
    """
    try:
        video = _resolve_file_path(mv)
    except HTTPException:
        return None
    candidate = find_sidecar_cover(video)
    if candidate is None:
        return None
    try:
        ext = candidate.suffix.lower() or ".jpg"
        if ext not in COVER_EXTENSIONS:
            ext = ".jpg"
        thumb_rel = Path("thumbnails") / f"{mv.id}{ext}"
        thumb_abs = settings.thumbnail_dir.parent / thumb_rel
        thumb_abs.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(candidate), str(thumb_abs))
        mv.thumbnail_path = str(thumb_rel)
        db.commit()
        logger.info("回退使用同名前盖 mv=%s: %s", mv.id, candidate)
        return thumb_abs
    except Exception as e:  # noqa: BLE001
        logger.warning("登记同名前盖失败 mv=%s: %s", mv.id, e)
        return candidate

@router.get("/{mv_id}/thumbnail")
def get_thumbnail(
    mv_id: int,
    db: DbDep,
    request: Request,
    w: Optional[int] = Query(
        None,
        ge=64,
        le=1920,
        description="按展示宽度出缩放变体（列表位用）。省略则以原分辨率返回。",
    ),
):
    """视频封面：可选 `w` 按展示宽度出 JPEG 变体，响应带 ETag 协商（命中返回 304）。

    封面落盘是视频原始分辨率（1080p/4K）或 sidecar 原图，而列表位只展示
    180~320 CSS px；不传 `w` 时行为与以往一致（原图 + private 缓存语义）。
    传 `w` 时从原图下采样一次并缓存到 derived/video-thumb-cache/，
    源图比 `w` 还小时不放大（resized_variant 返回 None，回退原图）。
    """
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    p = ensure_video_thumbnail(db, mv)
    if p is None:
        p = _find_sibling_cover(mv, db)
    if p is None:
        raise HTTPException(404, "暂无封面")
    if w:
        variant = resized_variant(
            p,
            w,
            quality=COVER_VARIANT_QUALITY,
            cache_dirname=COVER_VARIANT_CACHE_DIR,
        )
        if variant is not None:
            return serve_derived_image(
                request, variant, "image/jpeg", cache_control=COVER_CACHE_CONTROL
            )
    media_type = {
        ".webp": "image/webp",
        ".avif": "image/avif",
        ".png": "image/png",
        ".gif": "image/gif",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }.get(p.suffix.lower(), "image/jpeg")
    return serve_derived_image(request, p, media_type, cache_control=COVER_CACHE_CONTROL)


@router.get("/{mv_id}/focus", response_model=FocusResponse)
def get_video_focus(mv_id: int, db: DbDep):
    """封面焦点（0~1 归一化人脸质心）；无缓存时按需检测一次并落库。

    检测不到人脸时返回 None 坐标，前端回退居中显示。
    """
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    from app.services.video_focus import ensure_focus

    focus = ensure_focus(db, mv)
    if focus is None:
        return FocusResponse(focus_x=None, focus_y=None)
    return FocusResponse(focus_x=focus[0], focus_y=focus[1])


@router.post("/{mv_id}/cover-frames", response_model=CoverFramesResponse)
def create_cover_frames(mv_id: int, payload: CoverFramesRequest, db: DbDep):
    """随机抽取候选帧小图（480px 宽），供用户挑选封面。exclude 传上一批时间点可实现「换一批不重复」。"""
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    count = max(1, min(int(payload.count or 8), 12))
    try:
        items = generate_cover_frames(mv, count=count, exclude=payload.exclude or [])
    except FFmpegError as e:
        raise HTTPException(400, str(e))
    return CoverFramesResponse(items=[CoverFrameItem(**i) for i in items])


@router.get("/{mv_id}/cover-frames/{name}")
def get_cover_frame(mv_id: int, name: str, db: DbDep):
    """访问单张候选帧小图（临时文件，应用或关闭弹窗后即清理）。"""
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    p = cover_frame_path(mv.id, name)
    if p is None:
        raise HTTPException(404, "候选帧不存在或已过期，请重新抽取")
    return FileResponse(
        p,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.post("/{mv_id}/cover-frames/apply", response_model=MessageResponse)
def apply_cover_frame(mv_id: int, payload: CoverFrameApplyRequest, db: DbDep):
    """将选中的时间点按原始分辨率重抽为正式封面，并锁定为手动封面（自动刷新不再覆盖）。"""
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    try:
        apply_manual_cover(db, mv, payload.at_seconds)
    except FFmpegError as e:
        raise HTTPException(400, str(e))
    return MessageResponse(message="封面已更新")


@router.delete("/{mv_id}/cover-manual", response_model=MessageResponse)
def reset_cover_manual(mv_id: int, db: DbDep):
    """清除手动封面标记，恢复自动行为（同名图片优先，否则自动抽帧）。"""
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    clear_manual_cover(db, mv)
    return MessageResponse(message="已恢复自动封面")

@router.post("/{mv_id}/open-in-system-player", response_model=MessageResponse)
def open_in_system_player(mv_id: int, db: DbDep):
    """用系统默认播放器打开原文件（保底方案）。"""
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    path = _resolve_file_path(mv)
    if not path.exists():
        raise HTTPException(404, f"文件不存在: {path}")

    try:
        system = platform.system()
        if system == "Windows":
            # os.startfile 用默认关联程序打开（视频会用默认播放器）
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            opener = "open" if system == "Darwin" else "xdg-open"
            # NAS / 容器等无桌面环境通常没有 xdg-open，此时该功能不可用，
            # 直接给出明确提示，避免 500 报错（FileNotFoundError: xdg-open）。
            if shutil.which(opener) is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"当前部署环境不支持用系统播放器打开（缺少 {opener}，无桌面环境）",
                )
            subprocess.Popen([opener, str(path)], close_fds=True)
        return MessageResponse(message="已尝试打开", detail={"path": str(path)})
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"打开失败: {e}") from e
