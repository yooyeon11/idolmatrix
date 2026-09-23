"""Album 路由：CRUD + 曲目管理。"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.guards import require_active, validate_release_artist
from app.core.deps import DbDep, PaginationDep
from app.models.album import Album, AlbumTrack
from app.models.company import Company
from app.models.music_video import MusicVideo
from app.models.song import Song
from app.schemas import (
    AlbumAlignTrackResult,
    AlbumAlignTracksRequest,
    AlbumAlignTracksResult,
    AlbumBrief,
    AlbumCreate,
    AlbumFuzzyHit,
    AlbumImportPreviewItem,
    AlbumImportReviewCandidate,
    AlbumImportReviewVideo,
    AlbumImportTrackItem,
    AlbumImportTrackResult,
    AlbumImportTracksRequest,
    AlbumImportTracksResult,
    AlbumRead,
    AlbumTrackCreate,
    AlbumTrackDetailRead,
    AlbumTrackRead,
    AlbumUpdate,
    CoverApplyRequest,
    CoverSearchItem,
    CoverSearchRequest,
    PageResponse,
)
from app.services import cover_service
from app.services.completion import album_completion_pct
from app.services.song_names import album_owner_names, song_owner_names
from app.services.soft_delete_service import soft_delete_album
from app.services.cover_service import ProviderError
from app.services.name_match import (
    match_song_for_album_import,
    normalize_song_name,
    song_related_video_ids,
)

router = APIRouter(prefix="/albums", tags=["albums"])


# ===== 专辑封面（获取/应用/读取/移除） =====


@router.post("/covers/search", response_model=List[CoverSearchItem])
def search_album_covers(payload: CoverSearchRequest):
    """搜索专辑封面候选：source 支持 netease / itunes。"""
    try:
        if payload.source == "itunes":
            return cover_service.search_itunes(payload.query)
        return cover_service.search_netease(payload.query)
    except ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"封面搜索失败：{e}") from e


@router.get("/{album_id}/cover")
def get_album_cover(album_id: int, db: DbDep):
    """读取专辑封面图片文件。"""
    album = Album.get_active(db, album_id)
    if not album:
        raise HTTPException(status_code=404, detail="Album 不存在")
    p = cover_service.resolve_cover_path(album.cover_path)
    if p is None:
        raise HTTPException(status_code=404, detail="该专辑暂无封面")
    return FileResponse(p, media_type=cover_service.media_type_of(p))


@router.post("/{album_id}/cover", response_model=AlbumRead)
def apply_album_cover(album_id: int, payload: CoverApplyRequest, db: DbDep):
    """下载用户选中的封面 URL 并绑定到专辑。"""
    album = Album.get_active(db, album_id)
    if not album:
        raise HTTPException(status_code=404, detail="Album 不存在")
    try:
        rel = cover_service.download_cover(payload.cover_url, album_id)
    except ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"封面下载失败：{e}") from e
    album.cover_path = rel
    db.commit()
    db.refresh(album)
    return album


@router.delete("/{album_id}/cover", response_model=AlbumRead)
def remove_album_cover(album_id: int, db: DbDep):
    """移除专辑封面（同时删除本地文件）。"""
    album = Album.get_active(db, album_id)
    if not album:
        raise HTTPException(status_code=404, detail="Album 不存在")
    p = cover_service.resolve_cover_path(album.cover_path)
    album.cover_path = None
    db.commit()
    if p is not None:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    db.refresh(album)
    return album


_NORM_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")
_ALBUM_SUFFIX_WORDS = ("minialbum", "fullalbum", "repackage", "special", "album")


def _normalize_album_name(s: Optional[str]) -> str:
    """归一化专辑名：小写、去非字母数字字符、剥离常见尾缀词，用于模糊查重。"""
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = _NORM_RE.sub("", s)
    changed = True
    while changed:
        changed = False
        for word in _ALBUM_SUFFIX_WORDS:
            if s.endswith(word) and len(s) - len(word) >= 4:
                s = s[: -len(word)]
                changed = True
                break
    return s


def _album_similarity(a: str, b: str) -> float:
    """两个归一化名称的相似度：相等 1.0；包含 0.95；否则 difflib 比值。"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) >= 4 and len(b) >= 4 and (a in b or b in a):
        return 0.95
    return difflib.SequenceMatcher(None, a, b).ratio()


_BRIEF_LIMIT = 50
# 曲目命中最多扫多少行（防止「a」这类单字把整张 album_tracks 全捞出来）
_SONG_HIT_SCAN = 300


def _briefs_with_album_owner(db: DbDep, albums: List[Album]) -> List[AlbumBrief]:
    """精简列表统一出口：每条都带专辑自身发行主体（`owner`，未填为 None）。"""
    album_owners = album_owner_names(db, [a.id for a in albums])
    out: List[AlbumBrief] = []
    for album in albums:
        brief = AlbumBrief.model_validate(album)
        brief.owner = album_owners.get(album.id)
        out.append(brief)
    return out


@router.get("/brief", response_model=list[AlbumBrief])
def list_brief(
    db: DbDep,
    q: Optional[str] = Query(None),
    song_hits: bool = Query(
        False,
        description="q 同时匹配专辑内曲目名：只知道歌名、不知道专辑名时用；"
        "命中项会带 matched_song_* 供前端拼提示语",
    ),
):
    """专辑精简列表（下拉 / 搜索用）。

    默认只按专辑名 / 中文名匹配。`song_hits=true` 时**额外**把「收录了名字含 q
    的歌曲」的专辑也捞出来 —— 用户只记得歌名（如 Gee）时也能找到对应专辑。
    专辑名直接命中的排在前、且不写 matched_song_*（它本来就靠名字被找到）。
    """
    stmt = select(Album).where(Album.active_filter())
    like = None
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Album.name.ilike(like) | Album.chinese_name.ilike(like))
    albums = list(db.scalars(stmt.order_by(Album.name).limit(_BRIEF_LIMIT)).all())

    if not (song_hits and like):
        return _briefs_with_album_owner(db, albums)

    hits: dict[int, tuple[Album, Optional[Song]]] = {a.id: (a, None) for a in albums}
    rows = db.execute(
        select(Album, Song)
        .join(AlbumTrack, AlbumTrack.album_id == Album.id)
        .join(Song, Song.id == AlbumTrack.song_id)
        .where(
            Album.deleted_at.is_(None),
            Song.deleted_at.is_(None),
            or_(Song.name.ilike(like), Song.chinese_name.ilike(like)),
        )
        .order_by(Song.name, Album.name)
        .limit(_SONG_HIT_SCAN)
    ).all()
    for album, song in rows:
        if len(hits) >= _BRIEF_LIMIT:
            break
        if album.id in hits:
            continue  # 专辑名已经命中，按专辑名口径展示
        hits[album.id] = (album, song)

    owners = song_owner_names(db, [s.id for _, s in hits.values() if s is not None])
    album_owners = album_owner_names(db, [a.id for a, _ in hits.values()])
    out: List[AlbumBrief] = []
    for album, song in hits.values():
        brief = AlbumBrief.model_validate(album)
        brief.owner = album_owners.get(album.id)
        if song is not None:
            brief.matched_song_id = song.id
            brief.matched_song_name = song.name
            brief.matched_song_owner = owners.get(song.id)
        out.append(brief)
    return out


@router.get("/fuzzy", response_model=list[AlbumFuzzyHit])
def fuzzy_albums(db: DbDep, q: Optional[str] = Query(None)):
    """按归一化名称模糊匹配专辑，供新建专辑前查重提示。"""
    needle = _normalize_album_name(q)
    if not needle:
        return []
    hits: List[tuple[Album, float]] = []
    for album in db.scalars(select(Album).where(Album.active_filter())).all():
        variants = [album.name, album.chinese_name, album.english_name, album.korean_name]
        variants += list(album.aliases or [])
        best = 0.0
        for v in variants:
            cand = _normalize_album_name(v)
            if not cand:
                continue
            best = max(best, _album_similarity(needle, cand))
        if best >= 0.6:
            hits.append((album, best))
    hits.sort(key=lambda x: x[1], reverse=True)
    return [
        AlbumFuzzyHit(
            id=a.id,
            name=a.name,
            chinese_name=a.chinese_name,
            album_type=a.album_type,
            score=round(s, 4),
        )
        for a, s in hits[:8]
    ]


@router.get("/by-uid/{uid}", response_model=AlbumRead)
def get_album_by_uid(uid: str, db: DbDep):
    """按永久业务 uid 查询；已软删除的实体默认不可见。"""
    album = Album.get_by_uid(db, uid)
    if not album:
        raise HTTPException(404, "Album 不存在")
    read = AlbumRead.model_validate(album)
    read.track_count = _album_track_counts(db, [album.id]).get(album.id, 0)
    return read


def _album_track_counts(db: DbDep, album_ids: List[int]) -> dict[int, int]:
    """批量统计专辑已收录曲目数（与曲目单口径一致：剔除软删除歌曲）。"""
    if not album_ids:
        return {}
    rows = db.execute(
        select(AlbumTrack.album_id, func.count(AlbumTrack.id))
        .join(Song, AlbumTrack.song_id == Song.id)
        .where(AlbumTrack.album_id.in_(album_ids), Song.deleted_at.is_(None))
        .group_by(AlbumTrack.album_id)
    ).all()
    return {aid: cnt for aid, cnt in rows}


@router.get("", response_model=PageResponse[AlbumRead])
def list_albums(
    db: DbDep,
    pagination: PaginationDep,
    q: Optional[str] = Query(None),
    album_type: Optional[str] = Query(None),
    sort: Optional[str] = Query(None, description="排序：completion=完成度升序，-completion=降序"),
):
    completion = album_completion_pct()
    stmt = select(Album, completion.label("completion_pct")).where(Album.active_filter())
    if q:
        stmt = stmt.where(Album.name.ilike(f"%{q}%"))
    if album_type:
        stmt = stmt.where(Album.album_type == album_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    if sort == "completion":
        stmt = stmt.order_by(completion.asc())
    elif sort == "-completion":
        stmt = stmt.order_by(completion.desc())
    else:
        stmt = stmt.order_by(Album.release_date.desc().nulls_last())
    rows = db.execute(
        stmt.offset(pagination.offset).limit(pagination.limit)
    ).all()
    counts = _album_track_counts(db, [album.id for album, _ in rows])
    items = []
    for album, pct in rows:
        read = AlbumRead.model_validate(album)
        read.completion_pct = round(pct)
        read.track_count = counts.get(album.id, 0)
        items.append(read)
    return PageResponse(
        items=items, total=total, page=pagination.page, page_size=pagination.page_size
    )


@router.post("", response_model=AlbumRead, status_code=201)
def create_album(payload: AlbumCreate, db: DbDep):
    a = Album(**payload.model_dump())
    validate_release_artist(db, a.release_artist_type, a.release_artist_id)
    if a.label_id is not None:
        require_active(db, Company, a.label_id, "Company(label)")
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


@router.get("/{album_id}", response_model=AlbumRead)
def get_album(album_id: int, db: DbDep):
    album = Album.get_active(db, album_id)
    if not album:
        raise HTTPException(404, "Album 不存在")
    read = AlbumRead.model_validate(album)
    read.track_count = _album_track_counts(db, [album.id]).get(album.id, 0)
    return read


@router.patch("/{album_id}", response_model=AlbumRead)
def update_album(album_id: int, payload: AlbumUpdate, db: DbDep):
    album = Album.get_active(db, album_id)
    if not album:
        raise HTTPException(404, "Album 不存在")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(album, k, v)
    validate_release_artist(db, album.release_artist_type, album.release_artist_id)
    if album.label_id is not None:
        require_active(db, Company, album.label_id, "Company(label)")
    db.commit()
    db.refresh(album)
    return album


@router.delete("/{album_id}", status_code=204)
def delete_album(album_id: int, db: DbDep):
    """软删除专辑：写入 deleted_at 墓碑（不级联拆曲目/不级联软删歌曲）。"""
    album = Album.get_active(db, album_id)
    if not album:
        raise HTTPException(404, "Album 不存在")
    soft_delete_album(db, album)
    db.commit()


# ===== Tracks =====
@router.get("/{album_id}/tracks", response_model=List[AlbumTrackDetailRead])
def list_tracks(album_id: int, db: DbDep):
    """曲目单：曲目行 + 关联歌曲摘要（软删除歌曲不展示）。"""
    if not Album.get_active(db, album_id):
        raise HTTPException(404, "Album 不存在或已删除")
    rows = db.execute(
        select(AlbumTrack, Song)
        .join(Song, AlbumTrack.song_id == Song.id)
        .where(
            AlbumTrack.album_id == album_id,
            Song.deleted_at.is_(None),
        )
        .order_by(AlbumTrack.disc_number, AlbumTrack.track_number)
    ).all()
    out: List[AlbumTrackDetailRead] = []
    for t, s in rows:
        read = AlbumTrackDetailRead.model_validate(t)
        read.song_name = s.name
        read.song_chinese_name = s.chinese_name
        read.song_duration = s.duration
        read.song_uid = s.uid
        out.append(read)
    return out


@router.post("/tracks", response_model=AlbumTrackRead, status_code=201, tags=["album-tracks"])
def add_track(payload: AlbumTrackCreate, db: DbDep):
    require_active(db, Album, payload.album_id, "Album")
    require_active(db, Song, payload.song_id, "Song")
    exists = db.scalar(
        select(AlbumTrack).where(
            AlbumTrack.album_id == payload.album_id,
            AlbumTrack.song_id == payload.song_id,
        )
    )
    if exists:
        raise HTTPException(409, "该歌曲已在此专辑中")
    t = AlbumTrack(**payload.model_dump())
    db.add(t)
    try:
        db.commit()
    except IntegrityError as e:
        # 并发添加 / 轨号撞 uq_album_track_position
        db.rollback()
        raise HTTPException(409, "曲目已存在或轨号冲突（并发修改），请重试") from e
    db.refresh(t)
    return t


@router.delete("/tracks/{track_id}", status_code=204, tags=["album-tracks"])
def remove_track(track_id: int, db: DbDep):
    t = db.get(AlbumTrack, track_id)
    if not t:
        raise HTTPException(404, "Track 不存在")
    db.delete(t)
    db.commit()


# ===== 曲目外部导入 =====


def _append_external_link(song: Song, source: Optional[str], external_track_id: Optional[str]) -> None:
    """把外部 trackId 记入 external_links（伪 ISRC），已存在则跳过。"""
    if not source or not external_track_id:
        return
    links = [l for l in (song.external_links or []) if isinstance(l, dict)]
    if any(
        l.get("source") == source and str(l.get("track_id")) == str(external_track_id)
        for l in links
    ):
        return
    links.append({"source": source, "track_id": str(external_track_id)})
    song.external_links = links


def _same_track_name(external: Optional[str], occupier: Optional[str]) -> bool:
    """外部曲目名与占位歌同名判定：归一化一致；符号名（如 @%）归一化为空时回退原始名。"""
    if not external or not occupier:
        return False
    en, on = normalize_song_name(external), normalize_song_name(occupier)
    if en and en == on:
        return True
    return external.strip().casefold() == occupier.strip().casefold()


def _backfill_song_meta(song: Song, t: AlbumImportTrackItem, album: Album, db) -> bool:
    """用外部曲目单补齐库内歌缺失的元数据（时长/发行主体/发行日期），不覆盖已有值。

    幂等：预览与导入共用；命中已在专辑内的同名歌时顺手治愈"视频自动建歌无时长"的旧账。
    """
    changed = False
    if song.duration is None and t.duration:
        song.duration = t.duration
        changed = True
    if not song.release_artist_type and album.release_artist_type:
        song.release_artist_type = album.release_artist_type
        song.release_artist_id = album.release_artist_id
        changed = True
    if song.release_date is None and album.release_date:
        song.release_date = album.release_date
        changed = True
    if changed:
        db.commit()
    return changed


@router.post(
    "/{album_id}/tracks/import-preview", response_model=List[AlbumImportPreviewItem]
)
def preview_import_tracks(album_id: int, payload: List[AlbumImportTrackItem], db: DbDep):
    """导入前查重（三态）+ 位置占用预检，供用户确认/人工裁决。"""
    album = Album.get_active(db, album_id)
    if not album:
        raise HTTPException(404, "Album 不存在")
    existing_song_ids = set(
        db.scalars(
            select(AlbumTrack.song_id).where(AlbumTrack.album_id == album_id)
        ).all()
    )
    pos_map = _album_track_positions(db, album_id)
    # 存疑候选的关联视频（批量取一次，供构造 review_candidates）
    all_candidates: List[Song] = []
    for t in payload:
        _, _, cands = match_song_for_album_import(
            db,
            t.name,
            duration=t.duration,
            release_artist_type=album.release_artist_type,
            release_artist_id=album.release_artist_id,
        )
        all_candidates.extend(cands)
    vid_map = song_related_video_ids(db, [c.id for c in all_candidates])
    video_names: dict[int, str] = {}
    flat_vids = sorted({v for vids in vid_map.values() for v in vids})
    if flat_vids:
        video_names = {
            vid: nm
            for vid, nm in db.execute(
                select(MusicVideo.id, MusicVideo.name).where(
                    MusicVideo.id.in_(flat_vids)
                )
            )
        }

    out: List[AlbumImportPreviewItem] = []
    for i, t in enumerate(payload):
        verdict, hit, candidates = match_song_for_album_import(
            db,
            t.name,
            duration=t.duration,
            release_artist_type=album.release_artist_type,
            release_artist_id=album.release_artist_id,
        )
        item = AlbumImportPreviewItem(
            index=i,
            name=t.name,
            disc_number=t.disc_number,
            track_number=t.track_number,
            duration=t.duration,
            match_status="new",
        )
        if verdict == "link" and hit is not None:
            item.matched_song_id = hit.id
            item.matched_song_name = hit.name
            item.matched_song_duration = hit.duration
            item.match_status = "in-album" if hit.id in existing_song_ids else "match"
        elif verdict == "review":
            item.match_status = "review"
            for c in candidates:
                vids = sorted(vid_map.get(c.id) or set())[-3:]
                item.review_candidates.append(
                    AlbumImportReviewCandidate(
                        song_id=c.id,
                        song_name=c.name,
                        song_chinese_name=c.chinese_name,
                        song_duration=c.duration,
                        videos=[
                            AlbumImportReviewVideo(
                                video_id=v, video_name=video_names.get(v)
                            )
                            for v in reversed(vids)
                        ],
                    )
                )
        # 占位同名解析：目标轨位上的库内歌与外部曲目同名 → 就是同一首已在专辑内，
        # 避免 review/空归一化误报与"被自己占位"的假冲突
        pos_key = (t.disc_number or 1, t.track_number)
        occupier = pos_map.get(pos_key)
        if occupier is not None and item.matched_song_id is None and _same_track_name(t.name, occupier[1]):
            item.matched_song_id = occupier[0]
            item.matched_song_name = occupier[1]
            item.matched_song_duration = None
            item.match_status = "in-album"
        # 已在专辑内/关联成功的歌：顺手回填缺失元数据（幂等，只填空字段）
        if item.matched_song_id is not None:
            hit_song = db.get(Song, item.matched_song_id)
            if hit_song is not None and item.match_status == "in-album":
                _backfill_song_meta(hit_song, t, album, db)
        # 位置占用预检
        if occupier is not None:
            if item.matched_song_id is not None and occupier[0] == item.matched_song_id:
                item.position_status = "same"
            else:
                item.position_status = "conflict"
                item.position_song_name = occupier[1]
        else:
            item.position_status = "free"
        out.append(item)
    return out


def _album_track_positions(
    db: DbDep, album_id: int
) -> dict[tuple[int, int], tuple[int, str]]:
    """专辑现有 (碟, 轨) → (song_id, 歌名) 映射（剔除软删除歌曲，与曲目单口径一致）。"""
    rows = db.execute(
        select(
            AlbumTrack.disc_number,
            AlbumTrack.track_number,
            AlbumTrack.song_id,
            Song.name,
        )
        .join(Song, AlbumTrack.song_id == Song.id)
        .where(AlbumTrack.album_id == album_id, Song.deleted_at.is_(None))
    ).all()
    return {(d, t): (sid, nm) for d, t, sid, nm in rows}


@router.post("/{album_id}/tracks/align-external", response_model=AlbumAlignTracksResult)
def align_album_tracks_to_external(
    album_id: int, payload: AlbumAlignTracksRequest, db: DbDep
):
    """按外部曲目单一键调序库内曲目。

    匹配口径（宽于导入查重）：已在本专辑曲目单中 + 归一化同名 → 视为同一首，
    按外部 (碟, 轨) 落位；同名在任一侧出现多次时保守不动，防止误并。
    未匹配曲目原位空闲则保持原位，被占用则顺延到本碟末尾空位。
    软删除歌曲残留的曲目行不参与匹配，但会避让（必要时同样顺延），防止占位撞键。
    仅 UPDATE 碟号/轨号两列，行主键与 song_id 不变，歌曲的视频、艺人等关联不受影响；
    两阶段更新（先挪临时大轨号再落位）避开 uq_album_track_position 冲突。
    """
    album = Album.get_active(db, album_id)
    if not album:
        raise HTTPException(404, "Album 不存在")

    rows = db.execute(
        select(
            AlbumTrack.id,
            AlbumTrack.disc_number,
            AlbumTrack.track_number,
            AlbumTrack.song_id,
            Song.name,
            Song.deleted_at,
        )
        .join(Song, AlbumTrack.song_id == Song.id)
        .where(AlbumTrack.album_id == album_id)
        .order_by(AlbumTrack.disc_number, AlbumTrack.track_number)
    ).all()
    if not rows:
        return AlbumAlignTracksResult()
    row_by_id = {r.id: r for r in rows}

    # 外部曲目单：归一化名 → 目标位置；同名或同位重复出现视为歧义
    ext_pos: dict[str, tuple[int, int]] = {}
    ambiguous: set[str] = set()
    seen_pos: set[tuple[int, int]] = set()
    for t in payload.tracks:
        norm = normalize_song_name(str(t.name or "").strip())
        pos_key = (t.disc_number or 1, t.track_number)
        if not norm or pos_key in seen_pos:
            continue
        seen_pos.add(pos_key)
        if norm in ext_pos or norm in ambiguous:
            ambiguous.add(norm)
            ext_pos.pop(norm, None)
        else:
            ext_pos[norm] = pos_key

    # 库内仅未删除歌曲参与按名匹配；同名多条 = 歧义
    norm_of: dict[int, str] = {
        r.id: normalize_song_name(str(r.name or "").strip())
        for r in rows
        if r.deleted_at is None
    }
    dup_norms = {
        n
        for n in set(norm_of.values())
        if n and sum(1 for v in norm_of.values() if v == n) > 1
    }

    # 分类：matched（按外部落位）/ skipped（歧义保守不动）/ unmatched（避让式顺延）
    matched: dict[int, tuple[int, int]] = {}
    skipped_ids: set[int] = set()
    unmatched: list[int] = []
    for r in rows:
        n = norm_of.get(r.id, "")
        if r.deleted_at is not None or not n:
            unmatched.append(r.id)
        elif n in ambiguous or n in dup_norms:
            skipped_ids.add(r.id)
        elif n in ext_pos:
            matched[r.id] = ext_pos[n]
        else:
            unmatched.append(r.id)

    # 依次占位：歧义行锁定原位 → 匹配行落外部位置（被占则降级为未匹配）
    final_pos: dict[int, tuple[int, int]] = {}
    claimed: set[tuple[int, int]] = set()
    for rid in skipped_ids:
        r = row_by_id[rid]
        final_pos[rid] = (r.disc_number, r.track_number)
        claimed.add((r.disc_number, r.track_number))
    demoted: set[int] = set()
    for rid, pos in matched.items():
        if pos in claimed:
            demoted.add(rid)
        else:
            final_pos[rid] = pos
            claimed.add(pos)

    # 未匹配行：原位空闲则保持，被占则顺延到本碟末尾
    for rid in sorted(
        unmatched + list(demoted),
        key=lambda i: (row_by_id[i].disc_number, row_by_id[i].track_number),
    ):
        r = row_by_id[rid]
        cur = (r.disc_number, r.track_number)
        if cur not in claimed:
            final_pos[rid] = cur
            claimed.add(cur)
            continue
        disc = cur[0]
        nxt = max((t for d, t in claimed if d == disc), default=0) + 1
        while (disc, nxt) in claimed:
            nxt += 1
        final_pos[rid] = (disc, nxt)
        claimed.add((disc, nxt))

    result = AlbumAlignTracksResult()
    for r in rows:
        pos = final_pos[r.id]
        if r.id in matched and r.id not in demoted:
            result.aligned.append(
                AlbumAlignTrackResult(
                    name=r.name,
                    song_id=r.song_id,
                    disc_number=pos[0],
                    track_number=pos[1],
                    action=(
                        "same" if pos == (r.disc_number, r.track_number) else "aligned"
                    ),
                )
            )
        elif r.id in skipped_ids:
            result.skipped.append(
                AlbumAlignTrackResult(
                    name=r.name,
                    song_id=r.song_id,
                    disc_number=r.disc_number,
                    track_number=r.track_number,
                    action="skipped",
                    detail="同名歌曲出现多次，为避免误并保持原位",
                )
            )
        elif r.deleted_at is None:
            if r.id in demoted:
                base = "外部目标位置已被占用"
            elif not norm_of.get(r.id):
                base = "歌名无有效匹配字符"
            else:
                base = "未出现在外部曲目单中"
            detail = f"{base}，保持原位"
            if pos != (r.disc_number, r.track_number):
                detail = f"{base}，顺延到碟{pos[0]}末尾"
            result.appended.append(
                AlbumAlignTrackResult(
                    name=r.name,
                    song_id=r.song_id,
                    disc_number=pos[0],
                    track_number=pos[1],
                    action="appended",
                    detail=detail,
                )
            )
        # 软删除歌曲的隐形行：静默避让，不进入结果

    moving = {
        rid: pos
        for rid, pos in final_pos.items()
        if pos != (row_by_id[rid].disc_number, row_by_id[rid].track_number)
    }
    if moving:
        try:
            trks = db.scalars(
                select(AlbumTrack).where(AlbumTrack.id.in_(list(moving)))
            ).all()
            # 阶段一：挪到目标碟的临时大轨号，腾出全部目标位置
            for i, t in enumerate(trks):
                t.disc_number = moving[t.id][0]
                t.track_number = 1_000_000 + i
            db.flush()
            # 阶段二：落位到最终 (碟, 轨)
            for t in trks:
                t.track_number = moving[t.id][1]
            db.flush()
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "调序失败：轨位冲突或并发修改，请重试")
    return result


@router.post("/{album_id}/tracks/import", response_model=AlbumImportTracksResult)
def import_album_tracks(album_id: int, payload: AlbumImportTracksRequest, db: DbDep):
    """批量导入外部曲目：三态判定 + 人工结论 + 位置幂等。

    单条失败不影响其他曲目（逐条提交）；新建歌曲与曲目在同一事务，
    曲目插入失败时一并回滚，不留半截数据。存疑条目未给人工结论时
    安全跳过（不自动关联，防止误并同名歌）。
    """
    album = Album.get_active(db, album_id)
    if not album:
        raise HTTPException(404, "Album 不存在")
    result = AlbumImportTracksResult()
    existing_song_ids = set(
        db.scalars(
            select(AlbumTrack.song_id).where(AlbumTrack.album_id == album_id)
        ).all()
    )
    pos_map = _album_track_positions(db, album_id)

    for t in payload.tracks:
        name = str(t.name).strip() if t.name else ""
        if not name:
            result.failed.append(AlbumImportTrackResult(name=t.name or "", detail="曲名为空，已跳过"))
            continue
        pos_key = (t.disc_number or 1, t.track_number)
        try:
            if t.matched_song_id:
                song = Song.get_active(db, t.matched_song_id)
                if not song:
                    result.failed.append(
                        AlbumImportTrackResult(name=name, detail="指定的关联歌曲不存在或已删除")
                    )
                    continue
            elif t.force_new:
                song = None
            else:
                verdict, song, _ = match_song_for_album_import(
                    db,
                    name,
                    duration=t.duration,
                    release_artist_type=album.release_artist_type,
                    release_artist_id=album.release_artist_id,
                )
                if verdict == "review":
                    # 占位同名解析（与预览同规则）：目标轨位上的库内歌与外部曲目同名
                    # → 同一首已在专辑内，走幂等+回填分支，不再要求人工确认
                    occ0 = pos_map.get(pos_key)
                    if occ0 is not None and _same_track_name(name, occ0[1]):
                        song = db.get(Song, occ0[0])
                    else:
                        result.skipped.append(
                            AlbumImportTrackResult(
                                name=name, detail="同名歌曲待人工确认，未导入"
                            )
                        )
                        continue
            if song is not None and song.id in existing_song_ids:
                # 已在专辑内：仍回填缺失元数据，不覆盖已有值
                _backfill_song_meta(song, t, album, db)
                result.skipped.append(
                    AlbumImportTrackResult(
                        name=name, song_id=song.id, detail="该歌曲已在此专辑中"
                    )
                )
                continue
            # 位置占用：同歌幂等跳过，异歌报具体冲突（先于新建，避免留下孤儿歌）
            occupier = pos_map.get(pos_key)
            if occupier is not None:
                if song is not None and occupier[0] == song.id:
                    result.skipped.append(
                        AlbumImportTrackResult(
                            name=name, song_id=song.id, detail="该位置已是此歌曲"
                        )
                    )
                else:
                    result.failed.append(
                        AlbumImportTrackResult(
                            name=name,
                            detail=(
                                f"碟{pos_key[0]}轨{pos_key[1]} 已被《{occupier[1]}》占用，"
                                "请先调整已有曲目"
                            ),
                        )
                    )
                continue
            created = False
            if song is None:
                song = Song(
                    name=name,
                    duration=t.duration,
                    release_artist_type=album.release_artist_type,
                    release_artist_id=album.release_artist_id,
                    release_date=album.release_date,
                )
                _append_external_link(song, payload.source, t.external_track_id)
                db.add(song)
                db.flush()  # 先落库拿到 song.id，再插 AlbumTrack
                created = True
            else:
                # 关联已有歌曲：仅补齐缺失的基础字段，不覆盖已有值
                if song.duration is None and t.duration:
                    song.duration = t.duration
                if not song.release_artist_type and album.release_artist_type:
                    song.release_artist_type = album.release_artist_type
                    song.release_artist_id = album.release_artist_id
                if song.release_date is None and album.release_date:
                    song.release_date = album.release_date
                _append_external_link(song, payload.source, t.external_track_id)

            db.add(
                AlbumTrack(
                    album_id=album_id,
                    song_id=song.id,
                    disc_number=t.disc_number or 1,
                    track_number=t.track_number,
                )
            )
            db.commit()
            existing_song_ids.add(song.id)
            pos_map[pos_key] = (song.id, song.name)
            entry = AlbumImportTrackResult(name=name, song_id=song.id)
            if created:
                result.created.append(entry)
            else:
                result.linked.append(entry)
        except IntegrityError as e:
            db.rollback()
            result.failed.append(
                AlbumImportTrackResult(
                    name=name,
                    detail=(
                        f"碟{pos_key[0]}轨{pos_key[1]} 轨号冲突或曲目重复"
                        "（并发修改），请重试"
                    ),
                )
            )
        except Exception as e:  # noqa: BLE001
            db.rollback()
            result.failed.append(AlbumImportTrackResult(name=name, detail=f"导入失败：{e}"))
    return result
