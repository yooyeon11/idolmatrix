"""MusicVideo 路由：CRUD + 系统播放器打开 + 封面。

播放相关能力（Direct Play / Direct Stream / Transcode）已统一收敛到
app.api.playback（PlaybackSession），本模块不再提供任何播放源端点。
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from fastapi.responses import FileResponse, Response
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, object_session, selectinload

from app.api.guards import require_active
from app.core.audit import mark_before, mark_changes
from app.core.config import settings
from app.core.deps import DbDep, PaginationDep
from app.core.ffmpeg import FFmpegNotFoundError, require_ffmpeg
from app.core.vocab import CAMERA_ANGLES, CAMERA_LABELS
from app.services.soft_delete_service import soft_delete_music_video
from app.services import video_publish_service
from app.services import video_scoring_service
from app.services import video_chapters_service
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
    ValueFacet,
    ValueFacetResponse,
    VideoPathSuggestion,
    VideoPublishRead,
    VideoRelocateRequest,
    VideoRelocateResult,
)
from app.services.derived_http import serve_derived_image
from app.services.file_service import (
    COVER_EXTENSIONS,
    find_sidecar_cover,
    find_sidecar_subtitles,
)
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
from app.services import related_service
from app.schemas.music_video import (
    MusicVideoRelated,
    MusicVideoRelatedArtist,
    MusicVideoRelatedWork,
)
from app.services.video_meta import (
    RESOLUTION_TIERS,
    RESOLUTION_UNKNOWN,
    cover_from_names,
    is_short_video,
    maybe_regenerate_session,
    normalize_video_types,
    resolution_ranges,
    resolution_tier,
    resolve_camera_angle,
    resolve_session_label,
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
COVER_CACHE_CONTROL = "private, max-age=604800"


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

def _fill_participants(mv: MusicVideo) -> None:
    """把 participants 里的库内艺人 id 解析成展示所需数据(uid/头像/当前名)。

    存储形态 [{id?: int, name: str}]:name 是选择时的快照;艺人仍在库时补充
    uid/头像并刷新为当前名,已删除则保留快照名(不阻断展示)。
    """
    parts = getattr(mv, "participants", None) or []
    if not parts:
        return
    ids = [int(p["id"]) for p in parts if isinstance(p, dict) and p.get("id")]
    artists: dict[int, Artist] = {}
    if ids:
        session = object_session(mv)
        if session is not None:
            for a in session.scalars(select(Artist).where(Artist.id.in_(ids))).all():
                artists[a.id] = a
    for p in parts:
        if not isinstance(p, dict):
            continue
        a = artists.get(p.get("id"))
        if a is not None and a.deleted_at is None:
            p["uid"] = a.uid
            p["avatar_path"] = a.avatar_path
            p["name"] = a.chinese_name or a.name


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
    if sort == "size_desc":
        return (MusicVideo.file_size.is_(None), MusicVideo.file_size.desc())
    if sort == "size_asc":
        return (MusicVideo.file_size.is_(None), MusicVideo.file_size.asc())
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
    camera_angle: Optional[str] = Query(
        None,
        description="逗号分隔的机位过滤（规范值，见 app/core/vocab.py）；`__none__` = 未标注",
    ),
    session_label: Optional[str] = Query(
        None, description="逗号分隔的场次过滤（`YYMMDD - 活动名`）"
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
        # 频道改名后按 channel_id 归组：旧名页面也能列出新名下的视频
        from app.services.uploader_channel_service import channel_identity_for_uploader

        _cid, _names = channel_identity_for_uploader(db, uploader)
        stmt = stmt.where(MusicVideo.original_uploader.in_(_names or [uploader]))
    stmt = _filter_is_short(stmt, is_short)
    stmt = _filter_resolution(stmt, resolution)
    stmt = _filter_camera(stmt, camera_angle)
    stmt = _filter_session(stmt, session_label)
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
        _fill_participants(mv)
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
        _fill_participants(mv)
    return items


def _parse_id_csv(raw: Optional[str]) -> list[int]:
    """逗号分隔 id 串 → int 列表（非法片段静默忽略）。"""
    if not raw:
        return []
    return [int(p) for p in (s.strip() for s in raw.split(",")) if p.isdigit()]


@router.get("/feed", response_model=list[MusicVideoRead])
def random_shorts_feed(
    db: DbDep,
    count: int = Query(10, ge=1, le=50),
    exclude_ids: Optional[str] = Query(
        None, description="逗号分隔的视频 id：本会话已刷过，不再返回"
    ),
):
    """随机短视频 feed（刷模式）。

    池子与短视频页同口径：library + is_short；ORDER BY RANDOM() 取 count 条，
    exclude_ids 用于会话内去重（前端每批上报已刷过的 id）。
    返回空 = 池子刷穿，由前端提示「重新洗牌」。
    """
    maybe_cleanup_missing_videos(db)
    stmt = (
        select(MusicVideo)
        .options(*_relations_options())
        .where(MusicVideo.deleted_at.is_(None))
    )
    stmt = stmt.where(MusicVideo.ingestion_status == "library")
    stmt = _filter_is_short(stmt, True)
    seen = _parse_id_csv(exclude_ids)
    if seen:
        stmt = stmt.where(~MusicVideo.id.in_(seen))
    items = db.scalars(stmt.order_by(func.random()).limit(count)).all()
    for mv in items:
        _fill_relations(mv)
        _fill_participants(mv)
    return [_to_read(mv) for mv in items]


@router.get("/{mv_id}/stage-matrix")
def get_video_stage_matrix(mv_id: int, db: DbDep):
    """打歌期三维矩阵：同曲(或同活动)视频按 演出日 × 成员 × 机位 分组。

    只读推导，机位由现有 video_type + 横竖屏归类，不依赖新增数据。
    available=False 表示无 song/event 锚点或池内仅自身，前端隐藏矩阵舱。
    """
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    from app.services import stage_matrix_service

    return stage_matrix_service.build_stage_matrix(db, mv)


@router.get("/{mv_id}/chapters")
def get_video_chapters(mv_id: int, db: DbDep):
    """视频章节（实验性，只读推导）：内嵌章节优先，简介时间轴兜底。"""
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    _fill_relations(mv)
    items = video_chapters_service.get_chapters(mv)
    return {"count": len(items), "items": items}


# ===== 外挂同名字幕（播放器字幕轨）=====
# 浏览器 <track> 只认 WebVTT，因此统一转成 VTT 出流：
# .vtt 原样、.srt 纯文本转换、.ass/.ssa 经 ffmpeg 转（保留文本与时间轴，丢特效样式）；
# .sub/.sup/.idx 是图形字幕，转不成文本轨，随视频归档但不进播放列表。


_SUB_RASTER_EXTS = {".sub", ".sup", ".idx"}


def _renderable_subtitles(video: Path) -> list[Path]:
    """可进播放器的同名字幕（文本类），按文件名排序保证 index 寻址稳定。"""
    return [
        p
        for p in find_sidecar_subtitles(video)
        if p.suffix.lower() not in _SUB_RASTER_EXTS
    ]


def _decode_text(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp936"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _srt_to_vtt(text: str) -> str:
    """SRT → WebVTT：补 WEBVTT 头，时间戳毫秒分隔逗号改点（SRT 缺少的话
    浏览器会把整个 cue 丢弃，这里不做字幕序号清理 —— WebVTT 对多余行宽容）。"""
    out = ["WEBVTT", ""]
    for line in text.splitlines():
        if "-->" in line:
            line = line.replace(",", ".")
        out.append(line)
    return "\n".join(out).strip() + "\n"


_VTT_TIME_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})\.(\d{3})")


def _shift_vtt_timestamps(text: str, offset: float) -> str:
    """把 VTT 时间戳整体平移 offset 秒。

    转码窗口的流内时间 = 片源绝对时间 - window_start，而 <track> 的调度
    跟随 video.currentTime（流内时间）—— 不平移的话字幕会早 offset 秒出现。
    """

    def shift(m: re.Match[str]) -> str:
        total = (
            int(m.group(1)) * 3600
            + int(m.group(2)) * 60
            + int(m.group(3))
            + offset
        )
        if total < 0:
            total = 0
        h, rem = divmod(total, 3600)
        mi, s = divmod(rem, 60)
        return f"{int(h):02d}:{int(mi):02d}:{int(s):02d}.{m.group(4)}"

    return _VTT_TIME_RE.sub(shift, text) if offset > 0 else text


def _ass_to_vtt_text(sub: Path, mv_id: int, index: int) -> str:
    """ass/ssa 经 ffmpeg 转 vtt，按「mv id + 序号 + 源 mtime」缓存到 derived。"""
    cache_dir = settings.derived_dir / "subtitles"
    stamp = int(sub.stat().st_mtime)
    cache = cache_dir / f"{mv_id}-{index}-{stamp}{sub.suffix.lower()}.vtt"
    if cache.is_file():
        return _decode_text(cache.read_bytes())
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        ffmpeg = require_ffmpeg()
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", str(sub), "-f", "webvtt", str(cache)],
            check=True,
            timeout=30,
            capture_output=True,
        )
    except (FFmpegNotFoundError, subprocess.SubprocessError, OSError) as e:
        raise HTTPException(500, "该字幕格式（ass/ssa）需要 ffmpeg 转换，当前环境不可用") from e
    return _decode_text(cache.read_bytes())


def _subtitle_label(video: Path, sub: Path) -> tuple[str, str]:
    """(展示名, 语言码)：取视频 stem 之后的中间段（.zh / .default / ''）。"""
    labels = {
        "zh": "中文",
        "zh-hans": "简中",
        "zh-hant": "繁中",
        "chs": "简中",
        "cht": "繁中",
        "cn": "简中",
        "gb": "简中",
        "big5": "繁中",
        "en": "英文",
        "eng": "英文",
        "kor": "韩语",
        "ko": "韩语",
        "jpn": "日语",
        "ja": "日语",
        "default": "默认",
    }
    mid = sub.stem[len(video.stem):].lstrip(".").lower()
    lang = mid or "zh"
    text = labels.get(mid) or (mid.upper() if mid else "")
    return (text or "字幕", lang)


@router.get("/{mv_id}/subtitles")
def list_video_subtitles(mv_id: int, db: DbDep):
    """外挂同名字幕轨列表（仅文本类：vtt/srt/ass/ssa），url 直出已转好的 VTT。"""
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    video = resolve_video_abs_path(mv)
    subs = _renderable_subtitles(video) if video is not None else []
    items = []
    for idx, sub in enumerate(subs):
        label, lang = _subtitle_label(video, sub)
        items.append(
            {
                "index": idx,
                "label": label,
                "language": lang,
                "url": f"/api/music-videos/{mv_id}/subtitles/{idx}.vtt",
                "default": ".default" in sub.stem.lower(),
            }
        )
    return {"items": items}


@router.get("/{mv_id}/subtitles/{name}")
def get_video_subtitle(
    mv_id: int,
    name: str,
    db: DbDep,
    offset: float = Query(0.0, ge=0, description="时间轴平移秒数（转码 window_start）"),
):
    """单条字幕的 VTT 出流；offset 供转码播放平移时间轴。index 寻址防文件名注入。"""
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    if not name.endswith(".vtt"):
        raise HTTPException(404, "字幕地址无效")
    try:
        index = int(name[: -len(".vtt")])
    except ValueError:
        raise HTTPException(404, "字幕地址无效") from None
    video = resolve_video_abs_path(mv)
    subs = _renderable_subtitles(video) if video is not None else []
    if index < 0 or index >= len(subs):
        raise HTTPException(404, "字幕不存在")
    sub = subs[index]
    try:
        if sub.suffix.lower() == ".srt":
            text = _shift_vtt_timestamps(_srt_to_vtt(_decode_text(sub.read_bytes())), offset)
        elif sub.suffix.lower() == ".ass" or sub.suffix.lower() == ".ssa":
            text = _shift_vtt_timestamps(_ass_to_vtt_text(sub, mv.id, index), offset)
        else:
            text = _shift_vtt_timestamps(_decode_text(sub.read_bytes()), offset)
    except OSError as e:
        raise HTTPException(404, f"字幕读取失败: {e}") from e
    return Response(
        content=text,
        media_type="text/vtt; charset=utf-8",
        headers={"Cache-Control": COVER_CACHE_CONTROL},
    )


class AiScoreRequest(BaseModel):
    """AI 评分请求：评分 AI 凭据由前端随请求带来（与入库 AI 同模式）。

    frames_mode: auto=按时长自适应（6~16 帧）；8/12/16=固定档位。
    """

    provider: str = "openai-compatible"
    base_url: str = ""
    api_key: Optional[str] = None
    model: str = ""
    frames_mode: str = "auto"


@router.post("/{mv_id}/ai-score")
def run_ai_score(mv_id: int, payload: AiScoreRequest, db: DbDep):
    """对单个视频执行 AI 评分（实验功能）：抽帧 → 视觉模型 → 落库。"""
    mv = _get_active(db, mv_id)
    creds = {
        "provider": payload.provider,
        "base_url": payload.base_url,
        "model": payload.model,
    }
    if payload.api_key:
        creds["api_key"] = payload.api_key
    try:
        row = video_scoring_service.score_video(db, mv, creds, frames_mode=payload.frames_mode)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"评分失败: {e}") from e
    return _score_payload(row)


@router.get("/{mv_id}/ai-score")
def get_ai_score(mv_id: int, db: DbDep):
    """取某视频最近一次 AI 评分（无则 null）。"""
    _get_active(db, mv_id)
    row = video_scoring_service.latest_score(db, mv_id)
    return _score_payload(row) if row is not None else None


class AiScoreResultRequest(BaseModel):
    """外部系统提交的评分结果（workbuddy 等）：dims 兼容整数或 {score, reason}。"""

    model: str
    dims: dict
    comment: str = ""
    frames: int = 0
    note: str = ""


@router.post("/{mv_id}/ai-score/result")
def submit_ai_score_result(
    mv_id: int, payload: AiScoreResultRequest, request: Request, db: DbDep
):
    """外部写入评分结果。免浏览器会话；须带 X-API-Token 头（设置 · API 令牌）。

    该路径在 deps.is_public 白名单内（中间件跳过会话/CSRF），鉴权在这里自验。
    新卡与旧版单 Token 都接受：统一走 get_external_principal，
    这样「卡没有 edit 权限」会得到 403 而不是含糊的 401。
    """
    from app.core.deps import get_external_principal

    provided = (request.headers.get("x-api-token") or "").strip()
    if not provided:
        raise HTTPException(401, "X-API-Token 缺失或不正确")
    get_external_principal(db, provided, request)
    mv = _get_active(db, mv_id)
    try:
        row = video_scoring_service.store_external_score(
            db,
            mv,
            model=payload.model,
            dims_raw=payload.dims,
            comment=payload.comment,
            frames=payload.frames,
            note=payload.note,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _score_payload(row)


def _score_payload(row) -> dict:
    return {
        "id": row.id,
        "music_video_id": row.music_video_id,
        "model": row.model,
        "dims": row.dims,
        "comment": row.comment,
        "frames": row.frames,
        "scored_at": row.scored_at.isoformat() if row.scored_at else None,
    }


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


# 机位筛选里的「未标注」哨兵值（前端下拉用同一个字符串）
CAMERA_NONE_KEY = "__none__"


def _filter_camera(stmt, camera_angle: Optional[str]):
    """按机位过滤（逗号分隔多选）。`__none__` = 未标注（camera_angle 为空）。"""
    if not camera_angle:
        return stmt
    keys = [k.strip() for k in camera_angle.split(",") if k.strip()]
    if not keys:
        return stmt
    clauses = []
    if CAMERA_NONE_KEY in keys:
        clauses.append(
            or_(MusicVideo.camera_angle.is_(None), MusicVideo.camera_angle == "")
        )
    values = [k for k in keys if k != CAMERA_NONE_KEY]
    if values:
        clauses.append(MusicVideo.camera_angle.in_(values))
    if not clauses:
        return stmt
    return stmt.where(or_(*clauses))


def _filter_session(stmt, session_label: Optional[str]):
    """按场次过滤（逗号分隔多选，值形如 `261017 - Music Bank`）。"""
    if not session_label:
        return stmt
    keys = [k.strip() for k in session_label.split(",") if k.strip()]
    if not keys:
        return stmt
    return stmt.where(MusicVideo.session_label.in_(keys))


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


@router.get("/camera-facets", response_model=ValueFacetResponse)
def camera_facets(
    db: DbDep,
    q: Optional[str] = Query(None),
    video_type: Optional[str] = Query(None),
    video_types: Optional[str] = Query(None),
    is_short: Optional[bool] = Query(None),
    ingestion_status: Optional[str] = Query(None),
):
    """机位筛选菜单：当前筛选条件下各机位的条数（含「未标注」）。

    顺序跟词表一致（`CAMERA_ANGLES`），未标注排最后；空机位不展示。
    """
    stmt = select(MusicVideo.camera_angle, func.count(MusicVideo.id)).where(
        MusicVideo.deleted_at.is_(None)
    )
    stmt = _filter_by_query_and_types(stmt, q, video_type, video_types)
    if ingestion_status:
        stmt = stmt.where(MusicVideo.ingestion_status == ingestion_status)
    stmt = _filter_is_short(stmt, is_short)
    rows = db.execute(stmt.group_by(MusicVideo.camera_angle)).all()

    counts: dict[str, int] = {}
    for value, cnt in rows:
        key = (value or "").strip() or CAMERA_NONE_KEY
        counts[key] = counts.get(key, 0) + int(cnt)

    items: list[ValueFacet] = []
    for camera in CAMERA_ANGLES:
        if counts.get(camera):
            items.append(
                ValueFacet(key=camera, count=counts[camera], label=CAMERA_LABELS.get(camera))
            )
    # 词表外的历史值（理论上写入时已拦住，外部直写可能留下）原样列出，避免计数丢失
    for key, cnt in counts.items():
        if key == CAMERA_NONE_KEY or key in CAMERA_ANGLES:
            continue
        items.append(ValueFacet(key=key, count=cnt))
    if counts.get(CAMERA_NONE_KEY):
        items.append(
            ValueFacet(key=CAMERA_NONE_KEY, count=counts[CAMERA_NONE_KEY], label="未标注")
        )
    return ValueFacetResponse(items=items)


@router.get("/session-facets", response_model=ValueFacetResponse)
def session_facets(
    db: DbDep,
    q: Optional[str] = Query(None),
    video_type: Optional[str] = Query(None),
    video_types: Optional[str] = Query(None),
    is_short: Optional[bool] = Query(None),
    ingestion_status: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    """场次筛选菜单：当前筛选条件下条数最多的 N 个场次（按条数倒序）。"""
    stmt = select(MusicVideo.session_label, func.count(MusicVideo.id)).where(
        MusicVideo.deleted_at.is_(None),
        MusicVideo.session_label.is_not(None),
        MusicVideo.session_label != "",
    )
    stmt = _filter_by_query_and_types(stmt, q, video_type, video_types)
    if ingestion_status:
        stmt = stmt.where(MusicVideo.ingestion_status == ingestion_status)
    stmt = _filter_is_short(stmt, is_short)
    rows = db.execute(
        stmt.group_by(MusicVideo.session_label)
        .order_by(func.count(MusicVideo.id).desc())
        .limit(limit)
    ).all()
    return ValueFacetResponse(
        items=[ValueFacet(key=str(label), count=int(cnt)) for label, cnt in rows if label]
    )


def _with_publish(db, mv: MusicVideo, read: MusicVideoRead) -> MusicVideoRead:
    """详情专用：回填 B 站分发记录。

    只在单条详情调用（按 id / 按 uid 两个入口都要走这里）。
    列表接口刻意不填——逐行查 video_publishes 会把分页查询退化成 N+1，
    而列表卡片本来也不展示 BV 号。
    """
    row = video_publish_service.get_publish(db, mv.id)
    if row is not None:
        read.publish = VideoPublishRead.model_validate(
            video_publish_service.to_read(row)
        )
    return read


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
    _fill_participants(mv)
    # ⚠ 前端详情页走的是这个接口（路由 /videos/:uid），不是 /{mv_id}，
    #   两处都必须回填 publish，否则「B 站播放源」永远显示未绑定。
    return _with_publish(db, mv, _to_read(mv))

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

@router.get("/{mv_id}/related", response_model=MusicVideoRelated)
def get_related(mv_id: int, db: DbDep):
    """右栏推荐：同曲 / 同组合（含小分队与成员 solo）/ 同公司 / 同场活动。"""
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    works, artists = related_service.collect_related(db, mv)
    return MusicVideoRelated(
        works=[
            MusicVideoRelatedWork(
                video=MusicVideoBrief.model_validate(video), reason=reason
            )
            for video, reason in works
        ],
        artists=[MusicVideoRelatedArtist(**item) for item in artists],
    )


@router.get("/{mv_id}", response_model=MusicVideoRead)
def get_video(mv_id: int, db: DbDep):
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    _fill_relations(mv)
    _fill_participants(mv)
    return _with_publish(db, mv, _to_read(mv))

@router.patch("/{mv_id}", response_model=MusicVideoRead)
def update_video(mv_id: int, payload: MusicVideoUpdate, request: Request, db: DbDep):
    mv = _get_active(db, mv_id)
    if not mv:
        raise HTTPException(404, "MusicVideo 不存在")
    data = payload.model_dump(exclude_unset=True)
    data, rel = _split_relations(data)
    # 审计：先抓旧值（后面的机位/场次归一化会改写 data，最终 diff 用归一化后的值）
    before = mark_before(mv, list(data.keys()))
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

    # 机位 / 场次（业主 2026-10-17 口径）：
    # - 显式改了机位 → 只认权威词表，词表外 400（由业主决定是否扩充词表）
    # - 显式改了场次 → 只校验格式（空串 = 清空）
    # - 没显式改场次但改了日期/活动名/频道名 → 原值是自动生成的才重算，手改过的保持
    if "camera_angle" in data:
        # ★ 业主 2026-10-23：**机位只对官方舞台（PerformanceVideo）填**；
        #   其他类型（Fancam / SpecialStage 特别舞台 / CoverStage / CollabStage /
        #   OfficialMV / ShortVideo …）一律清空。
        #   顺带修坑：原实现里 `camera_angle` 传 None/空串会被当成「没给值」→
        #   又按 name 尾标推回来，导致**根本清不掉**。现在显式空值 = 真清空。
        _vtype = data.get("video_type") or mv.video_type
        _raw = (data.get("camera_angle") or "").strip()
        if _vtype != "PerformanceVideo" or not _raw:
            data["camera_angle"] = None
        else:
            camera, unknown = resolve_camera_angle(data.get("name", mv.name), _raw)
            if unknown:
                raise HTTPException(
                    400,
                    f"机位「{unknown}」不在权威词表内，暂不入库。"
                    f"确认属于新机位后请扩充 app/core/vocab.py 的 CAMERA_ANGLES，"
                    f"或先把机位选为「其他」。",
                )
            data["camera_angle"] = camera
    if "session_label" in data and (data.get("session_label") or "").strip():
        label, err = resolve_session_label(data.get("session_label"))
        if err:
            raise HTTPException(400, err)
        data["session_label"] = label
    else:
        # 未提供或显式清空 → 按（新的）日期/活动名重算；
        # 只在「原值仍是自动生成的那一个」时覆盖，手改过的保持不动
        data.pop("session_label", None)
        regenerated = maybe_regenerate_session(
            old_label=mv.session_label,
            old_performance_date=mv.performance_date,
            old_published_date=mv.published_date,
            old_event_name=mv.event_name,
            old_uploader=mv.original_uploader,
            new_performance_date=data.get("performance_date", mv.performance_date),
            new_published_date=data.get("published_date", mv.published_date),
            new_event_name=data.get("event_name", mv.event_name),
            new_uploader=data.get("original_uploader", mv.original_uploader),
        )
        if regenerated is not None:
            data["session_label"] = regenerated

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
    _fill_participants(mv)
    audit_changes = dict(data)
    if rel:
        rel_changed = {k: v for k, v in rel.items() if v is not None}
        if rel_changed:
            audit_changes["relations"] = rel_changed
    mark_changes(request, before, audit_changes)
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
