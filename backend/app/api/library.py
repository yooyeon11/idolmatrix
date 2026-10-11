"""库管理路由：扫描待整理 + 入库。"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.deps import DbDep
from app.models.incoming_file import IncomingFile
from app.schemas import (
    BuildTitleRequest,
    BuildTitleResult,
    DuplicateCopyInfo,
    IncomingInfoItem,
    IncomingInfoRequest,
    IngestRequest,
    IngestResult,
    LibraryCleanupResult,
    MatchHintsResult,
    PathPreviewRequest,
    PathPreviewResult,
    ReorganizeExecuteRequest,
    ReorganizePlanItem,
    ReorganizeResult,
    ScannedFileItem,
    DeleteScannedRequest,
)
from app.services import audiodb_service
from app.services.derived_http import serve_derived_image
from app.services.incoming_match_service import build_match_hints
from app.services.file_service import (
    _sanitize,
    find_sidecar_cover,
    find_sidecar_subtitles,
    list_library_dirs,
    match_existing_dir,
)
from app.services.incoming_scan_service import sync_incoming_files
from app.services.library_service import (
    ArtistDirNameConflict,
    auto_organize_rel,
    cleanup_missing_videos,
    execute_reorganize,
    extract_source_identity,
    ingest_video,
    library_duplicate_index,
    plan_reorganize,
    read_incoming_info,
)

router = APIRouter(prefix="/library", tags=["library"])

logger = logging.getLogger(__name__)


@router.get("/scan", response_model=List[ScannedFileItem])
def scan_endpoint(db: DbDep):
    """读取 incoming_files 缓存表，返回所有待整理文件及其技术字段。

    is_duplicate 在读侧按当前库复核：扫描落库的是快照，入库 / 清空失效视频 /
    回收站恢复之后可能过期（比如文件失效清空后，旧的「已存在」标记残留）。
    复核口径（三路任一命中即提示）：平台视频 ID（source_platform+source_id，
    双方 casefold）/ 文件名+大小 / 大小+时长(±1秒)。
    发现与快照不一致时顺带回写缓存表。

    issues 为读侧健康检查（按文件系统现状即时判断，不落表）：
    - missing_json：同名 info.json 缺失（yt-dlp / bili-sync 产物应有）
    - missing_cover：同名封面缺失（find_sidecar_cover 同名/同名后缀式口径）
    - suspicious_file：探测元数据全空（duration/width/height 一个都没有 →
      很可能是拖动中未下载完的残缺文件，ffprobe 读不出流信息）
    """
    rows = db.scalars(select(IncomingFile).order_by(IncomingFile.file_path)).all()
    dup_index = library_duplicate_index(db)
    dirty = False
    items: List[ScannedFileItem] = []
    for r in rows:
        video_path = Path(r.file_path)
        # 复核口径：平台视频ID / 文件名+大小 / 大小+时长(±1秒) —— 快照可能过期，
        # 入库 / 清空失效视频 / 回收站恢复之后按当前库重算。
        # 升级后旧行还没有平台视频身份（NULL）：读侧兜底读一次侧车元数据并回填；
        # 读不到置空串哨兵（= 确实无元数据），之后不再重读。
        if r.source_id is None:
            try:
                info = read_incoming_info(video_path)
            except Exception:  # noqa: BLE001
                info = {}
            r.source_platform, r.source_id = extract_source_identity(info)
            r.source_id = r.source_id or ""
            dirty = True
        dup_row = dup_index.find_duplicate(
            r.file_name,
            r.file_size,
            r.duration,
            source_platform=r.source_platform,
            source_id=r.source_id or None,
        )
        dup = dup_row is not None
        if dup != r.is_duplicate:
            r.is_duplicate = dup
            dirty = True
        duplicate_of = None
        if dup_row is not None:
            duplicate_of = DuplicateCopyInfo(
                id=dup_row.id,
                uid=dup_row.uid,
                name=dup_row.name,
                file_size=dup_row.file_size,
                duration=dup_row.duration,
                width=dup_row.width,
                height=dup_row.height,
            )
        issues: List[str] = []
        sidecar_json = next(
            (
                p
                for p in (
                    video_path.with_suffix(".json"),
                    video_path.parent / f"{video_path.stem}.info.json",
                )
                if p.is_file()
            ),
            None,
        )
        if sidecar_json is None:
            issues.append("missing_json")
        if find_sidecar_cover(r.file_path) is None:
            issues.append("missing_cover")
        # ffprobe 字段一个都没拿到 → 残缺/非视频文件（拖动中未下载完等）
        if (
            r.probed_at is None
            and r.duration is None
            and r.width is None
            and r.height is None
        ):
            issues.append("suspicious_file")
        items.append(
            ScannedFileItem(
                path=r.file_path,
                file_name=r.file_name,
                file_size=r.file_size,
                duration=int(r.duration) if r.duration is not None else None,
                width=r.width,
                height=r.height,
                video_codec=r.video_codec,
                audio_codec=r.audio_codec,
                is_duplicate=dup,
                duplicate_of=duplicate_of,
                issues=issues,
                created_at=r.created_at.isoformat() if r.created_at else None,
            )
        )
    if dirty:
        db.commit()
    return items


@router.post("/scan/sync")
def scan_sync_endpoint(db: DbDep):
    """手动触发一次 incoming 目录扫描落库，返回统计结果。

    与后台周期线程共用 _scan_lock，扫描进行中时返回 skipped_running。
    """
    return sync_incoming_files(db)


@router.post("/info", response_model=IncomingInfoItem)
def incoming_info_endpoint(payload: IncomingInfoRequest):
    """读取待整理文件的 info.json 元数据（精简字段，不含评论等）。"""
    try:
        source = Path(payload.file_path).resolve()
        info = read_incoming_info(source)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"读取元数据失败: {e}") from e
    # 本地同名封面探测（foo.webp / foo.mkv.webp 等）：详情页优先展示本地封面，避免每次拉取远程缩略图
    info["has_local_cover"] = find_sidecar_cover(source) is not None
    return info


@router.get("/match-hints", response_model=MatchHintsResult)
def match_hints_endpoint(
    db: DbDep,
    file_path: str = Query(..., description="待整理视频文件的绝对路径"),
):
    """库驱动本地匹配：从文件名/标题识别艺人、歌曲，并按资料库关系带出专辑。

    不看发布者/频道。与 AI 建议互补：专辑来自库内 AlbumTrack 关系（确定性），AI 不可覆盖。
    """
    source = Path(file_path).resolve()
    if settings.resolve_incoming_root(source) is None:
        raise HTTPException(400, f"文件不在 incoming 目录内: {source}")
    if not source.exists():
        raise HTTPException(404, f"源文件不存在: {source}")
    try:
        info = read_incoming_info(source)
    except (FileNotFoundError, ValueError):
        # 没有 info.json（或解析失败）：文件名与技术参数是唯一线索
        info = {}
    duration = None
    if info.get("duration"):
        try:
            duration = int(info["duration"])
        except (TypeError, ValueError):
            duration = None
    from app.services.app_settings import read_ingest_match_mode

    return build_match_hints(
        db,
        source.name,
        info,
        duration=duration,
        match_mode=read_ingest_match_mode(db),
    )


@router.get("/local-cover")
def local_cover_endpoint(request: Request, file_path: str = Query(..., description="待整理视频文件的绝对路径")):
    """返回与视频同名的本地封面图（foo.webp / foo.mkv.webp 等），带 ETag 条件请求协商。"""
    source = Path(file_path).resolve()
    if settings.resolve_incoming_root(source) is None:
        raise HTTPException(400, f"文件不在 incoming 目录内: {source}")
    cover = find_sidecar_cover(source)
    if cover is None:
        raise HTTPException(404, "未找到本地同名封面")
    return serve_derived_image(request, cover, audiodb_service.media_type_of(cover))


@router.post("/ingest", response_model=IngestResult, status_code=201)
def ingest_endpoint(payload: IngestRequest, db: DbDep):
    """入库：创建 MusicVideo + 移动文件 + 截封面。"""
    try:
        return ingest_video(db, payload)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except IntegrityError as e:
        # 预检后、提交前的并发重复（file_hash / source_platform+source_id 撞唯一索引）
        db.rollback()
        raise HTTPException(409, "该视频已被并发入库（重复），请刷新后重试") from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"入库失败: {e}") from e


@router.post("/preview-path", response_model=PathPreviewResult)
def preview_path_endpoint(payload: PathPreviewRequest, db: DbDep):
    """计算入库建议路径（相对 + 绝对，POSIX 格式），供入库表单展示。

    与入库 / 更改存储路径 / 存量重整共用 auto_organize_rel 单一实现：
    按表单当前关联构造未入库的临时 MusicVideo 参与规则推导，不写库。
    """
    from datetime import date as _date

    from app.models.artist import Artist
    from app.models.group import Group
    from app.models.music_video import MusicVideo
    from app.models.song import Song
    from app.services.video_meta import is_short_video, normalize_video_types

    video_type, video_types = normalize_video_types(
        payload.video_types, payload.video_type
    )
    perf_date = None
    if payload.performance_date:
        try:
            perf_date = _date.fromisoformat(str(payload.performance_date).strip())
        except ValueError:
            perf_date = None

    is_short = payload.is_short
    if is_short is None:
        # 短视频判定唯一口径：只看 video_types 是否含 ShortVideo（不再按时长兜底）
        is_short = is_short_video(video_types, video_type)

    mv = MusicVideo(
        name=payload.title or payload.file_name,
        video_type=video_type,
        video_types=video_types,
        is_solo=bool(payload.is_solo),
        is_short=is_short,
        performance_date=perf_date,
    )
    mv.artists = [
        a for a in (db.get(Artist, i) for i in (payload.artist_ids or [])) if a is not None
    ]
    mv.groups = [
        g for g in (db.get(Group, i) for i in (payload.group_ids or [])) if g is not None
    ]
    mv.songs = [
        s for s in (db.get(Song, i) for i in (payload.song_ids or [])) if s is not None
    ]
    # 「待新建」草稿实体以临时对象参与规则推导（仅用于路径计算，不写库）：
    # 入库时会先把草稿建成真实实体再算路径，预览口径必须一致，
    # 否则 AI 识别出的全新艺人/组合（库里还没有）无法触发归档规则，
    # 预览会错误地平铺到 library 根目录
    for draft_name in payload.draft_artist_names or []:
        name = (draft_name or "").strip()
        if name and not any((a.name or "") == name for a in mv.artists):
            mv.artists.append(Artist(name=name))
    for draft_name in payload.draft_group_names or []:
        name = (draft_name or "").strip()
        if name and not any((g.name or "") == name for g in mv.groups):
            mv.groups.append(Group(name=name))
    for draft_name in payload.draft_song_names or []:
        name = (draft_name or "").strip()
        if name:
            mv.songs.append(Song(name=name))

    # 重名艺人消歧（v3.2.40）：同一个展示名的艺人，第一个保留原名，其后追加中文名
    # （Yuna(유나)）；重名艺人缺中文名时推导被阻断 → 预览直接说明原因，入库同样会被拦
    notice = None
    try:
        auto_dir = auto_organize_rel(mv, db=db)
    except ArtistDirNameConflict as exc:
        auto_dir = None
        notice = f"{exc}。"
    if auto_dir is not None:
        # 与入库同口径：大小写不敏感匹配正式库已有目录（已有 Twice/ 时不预览成 TWICE/）
        auto_dir = match_existing_dir(auto_dir)
        rel = auto_dir / (_sanitize(payload.file_name) or payload.file_name)
        abs_path = (settings.library_dir / rel).resolve()
    else:
        rel = Path(_sanitize(payload.file_name) or payload.file_name)
        abs_path = (settings.library_dir / rel).resolve()
        if notice is None:
            notice = "未关联艺人/组合，无法自动归档（入库前请补全关联）"
    return PathPreviewResult(
        relative_path=rel.as_posix(),
        absolute_path=abs_path.as_posix(),
        notice=notice,
    )


# ===== 标题重建（与 AI 标题规范「YYMMDD Artist - Song [Event Name Type]」同口径） =====

_TITLE_TYPE_ZH = {
    "OfficialMV": "官方MV",
    "PerformanceVideo": "官方舞台",
    "Fancam": "粉丝直拍",
    "SpecialStage": "特别舞台",
    "CollabStage": "合作舞台",
    "CoverStage": "翻唱",
    "MixEdit": "MIX混剪",
    "Teaser": "预告",
    "SpecialVideo": "非表演视频",
    "ShortVideo": "短视频",
    "Other": "其他视频",
}


def _title_artist_name(db: DbDep, payload: BuildTitleRequest) -> Optional[str]:
    """标题艺人段（按 AI 规范优先官方英文名）：已选艺人 > 组合。"""
    from app.models.artist import Artist
    from app.models.group import Group

    for aid in payload.artist_ids or []:
        artist = db.get(Artist, aid)
        if artist is not None:
            return artist.stage_name or artist.english_name or artist.name
    for gid in payload.group_ids or []:
        group = db.get(Group, gid)
        if group is not None:
            return group.english_name or group.name
    return None


def _title_song_names(db: DbDep, payload: BuildTitleRequest) -> List[str]:
    """标题歌曲段：按 AI 规范优先官方英文曲名；多首按序去重。"""
    from app.models.song import Song

    names: List[str] = []
    for sid in payload.song_ids or []:
        song = db.get(Song, sid)
        if song is None:
            continue
        name = song.english_name or song.name
        if name and name not in names:
            names.append(name)
    return names


def _title_date_prefix(payload: BuildTitleRequest) -> Optional[str]:
    """标题日期前缀：优先表演日期，其次发布/上传日期。"""
    for value in (payload.performance_date, payload.published_date):
        if value:
            prefix = _fmt_yymmdd(str(value))
            if prefix:
                return prefix
    return None


def _title_suffix(payload: BuildTitleRequest) -> str:
    """标题括号后缀 [Event Type]：事件名与类型中文名均可缺省。"""
    event = (payload.event_name or "").strip()
    vts = [t for t in (payload.video_types or []) if t]
    kind = _TITLE_TYPE_ZH.get(vts[0], vts[0]) if vts else None
    inner = " ".join(x for x in (event, kind) if x)
    return f"[{inner}]" if inner else ""


def _build_static_title(db: DbDep, payload: BuildTitleRequest) -> str:
    """按当前关联字段重建 AI 风格标题 YYMMDD Artist - Song [Event Type]；艺人缺失返回空串。"""
    artist = _title_artist_name(db, payload)
    if not artist:
        return ""
    songs = " & ".join(_title_song_names(db, payload))
    parts = []
    date_prefix = _title_date_prefix(payload)
    if date_prefix:
        parts.append(date_prefix)
    parts.append(f"{artist} - {songs}" if songs else artist)
    suffix = _title_suffix(payload)
    if suffix:
        parts.append(suffix)
    return " ".join(parts)


@router.post("/build-title", response_model=BuildTitleResult)
def build_title_endpoint(payload: BuildTitleRequest, db: DbDep):
    """按当前表单关联字段重建标题（name），供前端自动跟随 / 手动重建。"""
    name = _build_static_title(db, payload)
    if not name:
        return BuildTitleResult(name="")
    return BuildTitleResult(name=name)


def _fmt_yymmdd(value: Optional[str]) -> Optional[str]:
    """把 ISO 日期字符串（YYYY-MM-DD）转成 yymmdd（如 2026-08-21 → 260821）；无法解析返回 None。"""
    if not value:
        return None
    parts = str(value).strip().split("-")
    if len(parts) != 3:
        return None
    y, mo, d = parts
    if len(y) != 4 or not (y + mo + d).isdigit():
        return None
    return f"{y[2:]}{int(mo):02d}{int(d):02d}"


@router.get("/directories")
def list_directories():
    """返回正式库已有的子目录（相对路径），供用户选择入库位置。"""
    return list_library_dirs()


@router.get("/browse-dirs")
def browse_dirs(rel: str = Query("", description="相对 library_dir 的目录路径;空=根")):
    """逐级浏览正式库目录（文件夹选择对话框用）。

    返回当前层子目录列表;越界/不存在回退根目录,只读不写。
    """
    root = settings.library_dir
    if not root.exists():
        return {"rel": "", "dirs": []}
    root_resolved = root.resolve()
    try:
        target = (root / rel).resolve() if rel else root_resolved
    except (OSError, ValueError):
        target = root_resolved
    # 防穿越：解析后必须仍在 library_dir 内;否则回退根
    if not target.is_relative_to(root_resolved):
        target = root_resolved
    cur_rel = "" if target == root_resolved else target.relative_to(root_resolved).as_posix()
    dirs: list[str] = []
    try:
        dirs = sorted(
            (p.name for p in target.iterdir() if p.is_dir() and not p.name.startswith(".")),
            key=str.casefold,
        )
    except OSError:
        dirs = []
    return {"rel": cur_rel, "dirs": dirs}


@router.delete("/scan")
def delete_scanned_file(payload: DeleteScannedRequest, db: DbDep):
    """删除待整理文件：视频 + 同名 info.json + 同名封面一并删除。

    安全边界（坚决防误删）：
    1. 路径必须与扫描缓存表中的记录逐字匹配 —— 只允许删「扫描到过的 incoming 文件」；
    2. 解析后必须仍在 incoming 扫描根目录之内（防穿越出 incoming）；
    3. 伴随文件只按「同名 json / find_sidecar_cover 同名封面」两种口径删除，
       与入库时的伴随文件口径完全一致；同名不同后缀的视频一律不碰；
    4. 每个删除目标 resolve 后校验其父目录仍位于对应扫描根之内。
    删除即物理删除（不可恢复），调用方需先在前端二次确认。
    """
    video_path = Path(payload.path)
    # ① 必须是扫描缓存表记录过的路径
    row = db.scalar(select(IncomingFile).where(IncomingFile.file_path == payload.path))
    if row is None:
        raise HTTPException(404, "该路径不在待整理列表中（可能已被扫描移除），请刷新列表")
    if not video_path.is_file():
        # 缓存行已无对应文件：直接清掉缓存行，返回成功（幂等）
        db.delete(row)
        db.commit()
        return {"ok": True, "deleted": [], "note": "文件已不存在，仅清理了列表记录"}

    # ② 视频文件必须位于某个 incoming 扫描根之内
    video_resolved = video_path.resolve()
    scan_roots = [Path(p).resolve() for p in settings.incoming_scan_dirs]
    if not any(video_resolved.is_relative_to(root) for root in scan_roots):
        raise HTTPException(400, "路径不在 incoming 目录内，拒绝删除")

    deleted: list[str] = []
    # 视频本体
    video_resolved.unlink()
    deleted.append(video_path.name)

    # ③ 伴随文件：json 两种命名 + find_sidecar_cover 封面口径（与入库/迁移一致）
    stem = video_resolved.stem
    sidecars = [
        video_resolved.with_suffix(".json"),
        video_resolved.parent / f"{stem}.info.json",
    ]
    cover = find_sidecar_cover(video_resolved)
    if cover is not None:
        sidecars.append(cover)
    for sub in find_sidecar_subtitles(video_resolved):
        sidecars.append(sub)
    seen: set[Path] = set()
    for sidecar in sidecars:
        try:
            if not sidecar.is_file():
                continue
            resolved = sidecar.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            # 伴随文件也必须在扫描根内（resolve 后校验，防符号链接带出）
            if not any(resolved.is_relative_to(root) for root in scan_roots):
                continue
            resolved.unlink()
            deleted.append(sidecar.name)
        except OSError as e:
            logger.warning("伴随文件删除失败（保留原位）: %s（%s）", sidecar, e)

    # 清掉缓存行
    db.delete(row)
    db.commit()
    logger.info("待整理文件已删除: %s（含伴随 %d 个）", video_resolved, len(deleted) - 1)
    return {"ok": True, "deleted": deleted}


# ===== 存量重整：按现行规则批量纠偏 =====


@router.get("/reorganize/preview", response_model=List[ReorganizePlanItem])
def reorganize_preview_endpoint(db: DbDep):
    """存量重整 dry-run：列出「当前路径 vs 规则建议路径」不一致的已入库视频。

    只读不写库；rule_missed=True 的条目缺主体关联，需补全后重试。
    """
    return plan_reorganize(db)


@router.post("/reorganize/execute", response_model=ReorganizeResult)
def reorganize_execute_endpoint(payload: ReorganizeExecuteRequest, db: DbDep):
    """按规则建议路径批量移动指定视频（单条失败不影响其他，返回逐条结果）。"""
    if not payload.ids:
        raise HTTPException(400, "ids 不能为空")
    return execute_reorganize(db, payload.ids)


@router.get("/paths")
def get_paths():
    """返回当前配置的存储路径（供前端展示与设置页）。"""
    return {
        "incoming": str(settings.incoming_dir),
        "library": str(settings.library_dir),
        "derived": str(settings.derived_dir),
        "transcode": str(settings.transcode_dir),
        "thumbnails": str(settings.thumbnail_dir),
    }


@router.get("/missing", response_model=LibraryCleanupResult)
def list_missing_videos(db: DbDep):
    """预览文件已不存在、但仍留在库里的视频（不写库）。"""
    return cleanup_missing_videos(db, dry_run=True)


@router.post("/cleanup-missing", response_model=LibraryCleanupResult)
def cleanup_missing_endpoint(
    db: DbDep,
    force: bool = Query(False, description="忽略存储未挂载防护，强制软删除全部找不到文件的记录"),
    purge_files: bool = Query(
        False,
        description="同时删除缩略图与伴随 json/封面；仅当视频文件确认不存在，且须用户明确确认",
    ),
):
    """软删除磁盘文件已不存在的视频记录。默认不删本地文件。"""
    return cleanup_missing_videos(
        db, respect_mount_guard=not force, purge_files=purge_files
    )


# ===== 数据库关联视图：实体的一跳关系聚合 =====

def _db_rel_entity(e) -> dict:
    return {
        "id": e.id,
        "uid": e.uid,
        "name": e.name,
        "chinese_name": e.chinese_name,
    }


def _db_rel_issue(
    level: str,
    code: str,
    text: str,
    *,
    target: Optional[dict] = None,
    action: Optional[str] = None,
    meta: Optional[dict] = None,
) -> dict:
    """结构化体检问题：保留 level+text，并附带 code / target / action / meta。"""
    out: dict = {"level": level, "code": code, "text": text}
    if target is not None:
        out["target"] = target
    if action is not None:
        out["action"] = action
    if meta is not None:
        out["meta"] = meta
    return out


@router.get("/db-relations/{kind}/{entity_id}")
def get_db_relations(kind: str, entity_id: int, db: DbDep):
    """关联视图数据：某实体的一跳关系（组合↔成员/小分队/专辑，艺人↔组合/专辑/歌曲等）。

    kind ∈ songs / albums / artists / groups / companies。MV 不在数据库关联范围内。
    issues 项形如 { level, code, text, target?, action?, meta? }。
    """
    from app.models import Album, Artist, Group, Song
    from app.models.album import AlbumTrack
    from app.models.company import Company, GroupCompanyRelation
    from app.models.membership import GroupMembership
    from app.models.music_video import music_video_albums
    from app.models.music_video import music_video_groups
    from app.models.song import SongArtistRelation
    from app.models.music_video import music_video_artists  # noqa: F401

    def _artist_brief(a: Artist) -> dict:
        return {
            "id": a.id, "uid": a.uid, "name": a.name,
            "chinese_name": a.chinese_name, "stage_name": a.stage_name,
            "korean_name": a.korean_name, "avatar_path": a.avatar_path,
        }

    def _group_brief(g: Group) -> dict:
        return {
            "id": g.id, "uid": g.uid, "name": g.name,
            "chinese_name": g.chinese_name, "korean_name": g.korean_name,
            "group_type": g.group_type, "avatar_path": g.avatar_path,
        }

    def _members_by_group_ids(gids: list[int]) -> dict[int, list[tuple]]:
        """group_id -> [(membership, artist), ...]"""
        if not gids:
            return {}
        rows_m = db.execute(
            select(GroupMembership, Artist)
            .join(Artist, Artist.id == GroupMembership.artist_id)
            .where(GroupMembership.group_id.in_(gids), Artist.deleted_at.is_(None))
            .order_by(GroupMembership.join_date, Artist.name)
        ).all()
        out_m: dict[int, list[tuple]] = {}
        for ms, a in rows_m:
            out_m.setdefault(ms.group_id, []).append((ms, a))
        return out_m

    def _album_brief(al: Album, track_count: Optional[int] = None) -> dict:
        out = {
            "id": al.id, "uid": al.uid, "name": al.name,
            "chinese_name": al.chinese_name, "album_type": al.album_type,
            "release_date": al.release_date.isoformat() if al.release_date else None,
            "cover_path": al.cover_path,
        }
        if track_count is not None:
            out["track_count"] = track_count
        return out

    def _song_brief(s: Song, role: Optional[str] = None) -> dict:
        out = {
            "id": s.id, "uid": s.uid, "name": s.name,
            "chinese_name": s.chinese_name,
        }
        if role:
            out["role"] = role
        return out

    def _track_item(at: AlbumTrack, sg: Song) -> dict:
        return {
            "track_id": at.id,
            "disc_number": at.disc_number,
            "track_number": at.track_number,
            "id": sg.id,
            "uid": sg.uid,
            "name": sg.name,
            "chinese_name": sg.chinese_name,
        }

    def _member_item(ms: GroupMembership, a: Artist) -> dict:
        return {
            **_artist_brief(a),
            "membership_id": ms.id,
            "positions": ms.positions or [],
            "join_date": ms.join_date.isoformat() if ms.join_date else None,
            "leave_date": ms.leave_date.isoformat() if ms.leave_date else None,
            "status": ms.status,
        }

    issues: list[dict] = []
    payload: dict = {"kind": kind, "entity": None, "relations": {}, "issues": issues}

    if kind == "groups":
        group = db.scalar(select(Group).where(Group.id == entity_id, Group.deleted_at.is_(None)))
        if group is None:
            raise HTTPException(404, "Group 不存在")
        payload["entity"] = {
            **_group_brief(group),
            "debut_date": group.debut_date.isoformat() if group.debut_date else None,
            "parent_group_id": group.parent_group_id,
        }
        rel: dict = {}

        rows = db.execute(
            select(GroupMembership, Artist)
            .join(Artist, Artist.id == GroupMembership.artist_id)
            .where(GroupMembership.group_id == group.id, Artist.deleted_at.is_(None))
            .order_by(GroupMembership.join_date, Artist.name)
        ).all()
        current, former = [], []
        parent_artist_ids = set()
        for ms, a in rows:
            item = _member_item(ms, a)
            if ms.status == "Former":
                former.append(item)
            else:
                current.append(item)
                parent_artist_ids.add(a.id)
            if ms.join_date and ms.leave_date and ms.leave_date < ms.join_date:
                issues.append(_db_rel_issue(
                    "warn",
                    "member_date_inverted",
                    f"成员 {a.chinese_name or a.name} 的退出日期早于加入日期",
                    target={"kind": "artists", "id": a.id, "membership_id": ms.id},
                    action="edit_membership",
                    meta={"join_date": item["join_date"], "leave_date": item["leave_date"]},
                ))
        rel["members_current"] = current
        rel["members_former"] = former
        if not current:
            issues.append(_db_rel_issue(
                "warn",
                "group_no_active_members",
                "组合没有任何现任成员",
                target={"kind": "groups", "id": group.id},
                action="add_member",
            ))

        parent = None
        if group.parent_group_id:
            pg = db.scalar(select(Group).where(Group.id == group.parent_group_id))
            parent = _group_brief(pg) if pg else None
        rel["parent_group"] = parent
        subunits = db.scalars(
            select(Group).where(Group.parent_group_id == group.id, Group.deleted_at.is_(None))
        ).all()
        sub_members = _members_by_group_ids([g.id for g in subunits])
        rel["subunits"] = []
        for g in subunits:
            mems = []
            for ms, a in sub_members.get(g.id, []):
                mems.append(_artist_brief(a))
                # 小分队成员应属于母队（现任或前成员均可）
                if a.id not in parent_artist_ids and a.id not in {m["id"] for m in former}:
                    issues.append(_db_rel_issue(
                        "warn",
                        "subunit_member_not_in_parent",
                        f"小分队 {g.name} 成员 {a.chinese_name or a.name} 不在母队成员名单中",
                        target={"kind": "artists", "id": a.id, "group_id": g.id, "parent_group_id": group.id},
                        action="fix_parent_membership",
                        meta={"subunit_id": g.id, "artist_id": a.id},
                    ))
            rel["subunits"].append({
                **_group_brief(g),
                "members": mems,
                "member_count": len(mems),
            })
        rel["companies"] = [
            {"id": c.id, "name": c.name}
            for c in db.scalars(
                select(Company)
                .join(GroupCompanyRelation, GroupCompanyRelation.company_id == Company.id)
                .where(GroupCompanyRelation.group_id == group.id, Company.deleted_at.is_(None))
            ).all()
        ]

        # 关联专辑：组合署名歌曲 ∪ 组合 MV 挂载的专辑
        album_map: dict[int, dict] = {}
        song_ids = db.scalars(
            select(SongArtistRelation.song_id).where(SongArtistRelation.group_id == group.id)
        ).all()
        if song_ids:
            rows2 = db.execute(
                select(Album, func.count(AlbumTrack.id))
                .join(AlbumTrack, AlbumTrack.album_id == Album.id)
                .where(AlbumTrack.song_id.in_(song_ids), Album.deleted_at.is_(None))
                .group_by(Album.id)
            ).all()
            for al, cnt in rows2:
                album_map[al.id] = _album_brief(al, cnt)
        mv_album_rows = db.execute(
            select(Album, func.count(func.distinct(music_video_albums.c.music_video_id)))
            .join(music_video_albums, music_video_albums.c.album_id == Album.id)
            .join(music_video_groups, music_video_groups.c.music_video_id == music_video_albums.c.music_video_id)
            .where(music_video_groups.c.group_id == group.id, Album.deleted_at.is_(None))
            .group_by(Album.id)
        ).all()
        for al, cnt in mv_album_rows:
            if al.id in album_map:
                album_map[al.id]["track_count"] = max(album_map[al.id].get("track_count") or 0, 0)
            else:
                album_map[al.id] = _album_brief(al, 0)
        # 穿透到歌曲：每张专辑直接带曲目列表
        if album_map:
            track_rows = db.execute(
                select(AlbumTrack, Song)
                .join(Song, Song.id == AlbumTrack.song_id)
                .where(
                    AlbumTrack.album_id.in_(list(album_map)),
                    Song.deleted_at.is_(None),
                )
                .order_by(
                    AlbumTrack.album_id, AlbumTrack.disc_number, AlbumTrack.track_number
                )
            ).all()
            for at, sg in track_rows:
                album_map[at.album_id].setdefault("tracks", []).append(_track_item(at, sg))
        for al_id, alb in album_map.items():
            tracks = alb.get("tracks") or []
            alb["track_count"] = len(tracks) if tracks else (alb.get("track_count") or 0)
            if not tracks:
                issues.append(_db_rel_issue(
                    "warn",
                    "album_no_tracks",
                    f"专辑「{alb['name']}」没有任何歌曲关联",
                    target={"kind": "albums", "id": al_id},
                    action="add_tracks",
                ))
        rel["albums"] = sorted(album_map.values(), key=lambda x: (x.get("name") or "").lower())
        if not rel["albums"]:
            issues.append(_db_rel_issue(
                "info",
                "group_no_albums",
                "暂无可关联的专辑（按组合署名歌曲与组合 MV 聚合）",
                target={"kind": "groups", "id": group.id},
            ))

        payload["relations"] = rel

    elif kind == "artists":
        artist = db.scalar(select(Artist).where(Artist.id == entity_id, Artist.deleted_at.is_(None)))
        if artist is None:
            raise HTTPException(404, "Artist 不存在")
        payload["entity"] = {
            **_artist_brief(artist),
            "debut_date": artist.debut_date.isoformat() if artist.debut_date else None,
        }
        rel: dict = {}
        rows = db.execute(
            select(GroupMembership, Group)
            .join(Group, Group.id == GroupMembership.group_id)
            .where(GroupMembership.artist_id == artist.id, Group.deleted_at.is_(None))
            .order_by(GroupMembership.join_date, Group.name)
        ).all()
        current, former = [], []
        for ms, g in rows:
            item = {
                **_group_brief(g),
                "membership_id": ms.id,
                "positions": ms.positions or [],
                "join_date": ms.join_date.isoformat() if ms.join_date else None,
                "leave_date": ms.leave_date.isoformat() if ms.leave_date else None,
                "status": ms.status,
            }
            (former if ms.status == "Former" else current).append(item)
        rel["groups_current"] = current
        rel["groups_former"] = former
        if not current and not former:
            issues.append(_db_rel_issue(
                "info",
                "solo_no_groups",
                "不属于任何组合（solo 艺人属正常，可忽略）",
                target={"kind": "artists", "id": artist.id},
            ))

        song_ids = db.scalars(
            select(SongArtistRelation.song_id).where(SongArtistRelation.artist_id == artist.id)
        ).all()
        album_map: dict[int, dict] = {}
        if song_ids:
            rows2 = db.execute(
                select(Album, func.count(AlbumTrack.id))
                .join(AlbumTrack, AlbumTrack.album_id == Album.id)
                .where(AlbumTrack.song_id.in_(song_ids), Album.deleted_at.is_(None))
                .group_by(Album.id)
            ).all()
            for al, cnt in rows2:
                album_map[al.id] = _album_brief(al, cnt)
        rel["albums"] = sorted(album_map.values(), key=lambda x: (x.get("name") or "").lower())

        songs = db.execute(
            select(Song, SongArtistRelation.role)
            .join(SongArtistRelation, SongArtistRelation.song_id == Song.id)
            .where(SongArtistRelation.artist_id == artist.id, Song.deleted_at.is_(None))
            .order_by(Song.name)
            .limit(100)
        ).all()
        rel["songs"] = [_song_brief(s, role) for s, role in songs]
        payload["relations"] = rel

    elif kind == "albums":
        album = db.scalar(select(Album).where(Album.id == entity_id, Album.deleted_at.is_(None)))
        if album is None:
            raise HTTPException(404, "Album 不存在")
        payload["entity"] = {
            **_album_brief(album),
            "label": album.label.name if album.label_id and album.label else None,
        }
        rel: dict = {}
        tracks = db.execute(
            select(AlbumTrack, Song)
            .join(Song, Song.id == AlbumTrack.song_id)
            .where(AlbumTrack.album_id == album.id, Song.deleted_at.is_(None))
            .order_by(AlbumTrack.disc_number, AlbumTrack.track_number)
        ).all()
        rel["tracks"] = [_track_item(at, s) for at, s in tracks]
        if not rel["tracks"]:
            issues.append(_db_rel_issue(
                "warn",
                "album_no_tracks",
                "专辑没有任何曲目关联",
                target={"kind": "albums", "id": album.id},
                action="add_tracks",
            ))

        credited = db.scalars(
            select(SongArtistRelation.song_id).where(
                SongArtistRelation.song_id.in_([s.id for _, s in tracks] or [0])
            )
        ).all()
        if tracks and not credited:
            issues.append(_db_rel_issue(
                "warn",
                "song_no_credits",
                "专辑内所有歌曲都没有艺人/组合署名关联",
                target={"kind": "albums", "id": album.id},
                meta={"scope": "all_tracks"},
            ))
        payload["relations"] = rel

    elif kind == "songs":
        song = db.scalar(select(Song).where(Song.id == entity_id, Song.deleted_at.is_(None)))
        if song is None:
            raise HTTPException(404, "Song 不存在")
        payload["entity"] = _song_brief(song)
        rel: dict = {}
        albums = db.execute(
            select(Album, AlbumTrack)
            .join(AlbumTrack, AlbumTrack.album_id == Album.id)
            .where(AlbumTrack.song_id == song.id, Album.deleted_at.is_(None))
            .order_by(Album.release_date, Album.name)
        ).all()
        rel["albums"] = [
            {
                **_album_brief(al),
                "track_id": at.id,
                "disc_number": at.disc_number,
                "track_number": at.track_number,
            }
            for al, at in albums
        ]
        if not rel["albums"]:
            issues.append(_db_rel_issue(
                "info",
                "album_no_tracks",
                "未关联任何专辑（数字单曲属正常）",
                target={"kind": "songs", "id": song.id},
                meta={"note": "digital_single_ok", "scope": "song_albums"},
            ))
        credits = db.scalars(
            select(SongArtistRelation)
            .where(SongArtistRelation.song_id == song.id)
        ).all()
        credit_items: list[dict] = []
        for cr in credits:
            if cr.artist_id:
                a = db.get(Artist, cr.artist_id)
                if a and a.deleted_at is None:
                    credit_items.append({"kind": "artist", **_artist_brief(a), "role": cr.role})
            elif cr.group_id:
                g = db.get(Group, cr.group_id)
                if g and g.deleted_at is None:
                    credit_items.append({"kind": "group", **_group_brief(g), "role": cr.role})
        rel["credits"] = credit_items
        if not credit_items:
            issues.append(_db_rel_issue(
                "warn",
                "song_no_credits",
                "歌曲没有任何艺人/组合署名关联",
                target={"kind": "songs", "id": song.id},
                action="add_credits",
            ))
        payload["relations"] = rel

    elif kind == "companies":
        company = db.scalar(select(Company).where(Company.id == entity_id, Company.deleted_at.is_(None)))
        if company is None:
            raise HTTPException(404, "Company 不存在")
        payload["entity"] = {"id": company.id, "name": company.name, "chinese_name": company.chinese_name}
        rel: dict = {}
        rel["groups"] = [
            _group_brief(g)
            for g in db.scalars(
                select(Group)
                .join(GroupCompanyRelation, GroupCompanyRelation.group_id == Group.id)
                .where(GroupCompanyRelation.company_id == company.id, Group.deleted_at.is_(None))
            ).all()
        ]
        rel["albums_as_label"] = [
            _album_brief(al)
            for al in db.scalars(
                select(Album).where(Album.label_id == company.id, Album.deleted_at.is_(None))
            ).all()
        ]
        if not rel["groups"] and not rel["albums_as_label"]:
            issues.append({
                "level": "info",
                "text": "该公司暂未关联任何组合或专辑",
                "target": {"kind": "companies", "id": company.id},
            })
        payload["relations"] = rel

    else:
        raise HTTPException(400, f"不支持的实体类型: {kind}")

    return payload
