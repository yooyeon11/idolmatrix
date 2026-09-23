"""Song 路由：CRUD + 关系 + Credits。"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.guards import require_active, validate_release_artist
from app.core.deps import DbDep, PaginationDep
from app.services.soft_delete_service import soft_delete_song
from app.models.album import Album, AlbumTrack
from app.models.artist import Artist
from app.models.group import Group
from app.models.music_video import MusicVideo, MusicVideoTrack, music_video_songs
from app.models.song import Credits, Song, SongArtistRelation
from app.schemas import (
    AlbumBrief,
    CreditsCreate,
    CreditsRead,
    PageResponse,
    SongArtistRelationCreate,
    SongArtistRelationRead,
    SongBrief,
    SongCreate,
    SongFuzzyHit,
    SongRead,
    SongUpdate,
)
from app.services.name_match import fuzzy_songs
from app.services.song_names import song_owner_names
from app.services.completion import song_completion_pct
from app.services.group_tree import expand_group_ids

router = APIRouter(prefix="/songs", tags=["songs"])


def _song_video_counts(db: DbDep, song_ids: List[int]) -> dict[int, int]:
    """批量统计歌曲关联视频数：直接关联 / M2M / 曲目行三种口径合并去重，排除短视频。"""
    if not song_ids:
        return {}
    active = MusicVideo.deleted_at.is_(None)
    not_short = MusicVideo.is_short.is_not(True)
    pairs: dict[int, set[int]] = {sid: set() for sid in song_ids}
    for sid, vid in db.execute(
        select(MusicVideo.song_id, MusicVideo.id).where(
            active, not_short, MusicVideo.song_id.in_(song_ids)
        )
    ):
        if sid is not None:
            pairs[sid].add(vid)
    for sid, vid in db.execute(
        select(music_video_songs.c.song_id, music_video_songs.c.music_video_id)
        .join(MusicVideo, MusicVideo.id == music_video_songs.c.music_video_id)
        .where(
            active,
            not_short,
            music_video_songs.c.song_id.in_(song_ids),
        )
    ):
        pairs[sid].add(vid)
    for sid, vid in db.execute(
        select(MusicVideoTrack.song_id, MusicVideoTrack.music_video_id)
        .join(MusicVideo, MusicVideo.id == MusicVideoTrack.music_video_id)
        .where(
            active,
            not_short,
            MusicVideoTrack.song_id.in_(song_ids),
        )
    ):
        pairs[sid].add(vid)
    return {sid: len(vids) for sid, vids in pairs.items()}


def _linked_song_ids_subq():
    """有关联视频的歌曲 id 并集（直接关联 / M2M / 曲目行三种口径，排除软删与短视频）。

    用「视频侧一次预聚合 UNION + Song.id IN」替代「每首歌 3 个相关 EXISTS」：
    视频表远小于歌曲表，一次物化后逐歌探测变成内存索引查找。NAS 实测
    （2807 首 / 慢速随机读存储）：逐行 EXISTS 版本 1.47s，预聚合版本预期 <50ms。
    三路都显式排 NULL，保证 NOT IN（orphan）语义安全。
    """
    active = MusicVideo.deleted_at.is_(None)
    not_short = MusicVideo.is_short.is_not(True)
    direct = select(MusicVideo.song_id.label("sid")).where(
        active, not_short, MusicVideo.song_id.is_not(None)
    )
    m2m = (
        select(music_video_songs.c.song_id.label("sid"))
        .join(MusicVideo, MusicVideo.id == music_video_songs.c.music_video_id)
        .where(active, not_short, music_video_songs.c.song_id.is_not(None))
    )
    via_track = (
        select(MusicVideoTrack.song_id.label("sid"))
        .join(MusicVideo, MusicVideo.id == MusicVideoTrack.music_video_id)
        .where(active, not_short, MusicVideoTrack.song_id.is_not(None))
    )
    return direct.union(m2m, via_track)


def _song_relation_names(db: DbDep, song_ids: List[int]) -> dict[int, list[str]]:
    """批量取歌曲所属艺人/组合显示名（过滤已软删除主体，按 relation.order 排序）。"""
    if not song_ids:
        return {}
    out: dict[int, list[str]] = {}
    rows = db.execute(
        select(SongArtistRelation, Artist, Group)
        .outerjoin(Artist, SongArtistRelation.artist_id == Artist.id)
        .outerjoin(Group, SongArtistRelation.group_id == Group.id)
        .where(
            SongArtistRelation.song_id.in_(song_ids),
            or_(
                and_(
                    SongArtistRelation.artist_id.is_not(None),
                    Artist.deleted_at.is_(None),
                ),
                and_(
                    SongArtistRelation.group_id.is_not(None),
                    Group.deleted_at.is_(None),
                ),
            ),
        )
        .order_by(SongArtistRelation.song_id, SongArtistRelation.order)
    ).all()
    for rel, artist, group in rows:
        name = None
        if artist is not None:
            name = artist.chinese_name or artist.stage_name or artist.name
        elif group is not None:
            name = group.chinese_name or group.name
        if name:
            out.setdefault(rel.song_id, []).append(name)
    return out


@router.get("", response_model=PageResponse[SongRead])
def list_songs(
    db: DbDep,
    pagination: PaginationDep,
    q: Optional[str] = Query(None),
    song_type: Optional[str] = Query(None),
    artist_id: Optional[int] = Query(None, description="按发行/关联艺人筛选"),
    group_id: Optional[int] = Query(None, description="按发行/关联组合筛选"),
    album_id: Optional[int] = Query(None, description="按所属专辑筛选"),
    sort: Optional[str] = Query(None, description="排序：completion=完成度升序，-completion=降序"),
    only_with_videos: bool = Query(False, description="仅显示有关联视频（未软删除、非短视频）的歌曲"),
    filter: str = Query(
        "all",
        pattern="^(all|linked|orphan)$",
        description="数据过滤：all=全部；linked=有关联视频；orphan=无关联视频",
    ),
):
    completion = song_completion_pct()
    stmt = select(Song, completion.label("completion_pct")).where(Song.active_filter())
    # 与 _song_video_counts 同口径：直接关联 / M2M / 曲目行三种任一命中即算有关联，
    # 排除软删除视频与短视频；视频侧一次预聚合（见 _linked_song_ids_subq）
    linked = _linked_song_ids_subq()
    if only_with_videos or filter == "linked":
        stmt = stmt.where(Song.id.in_(linked))
    elif filter == "orphan":
        stmt = stmt.where(Song.id.not_in(linked))
    if q:
        stmt = stmt.where(
            Song.name.ilike(f"%{q}%") | Song.chinese_name.ilike(f"%{q}%")
        )
    if song_type:
        stmt = stmt.where(Song.song_type == song_type)
    if artist_id is not None:
        stmt = stmt.where(
            Song.id.in_(
                select(SongArtistRelation.song_id).where(
                    SongArtistRelation.artist_id == artist_id
                )
            )
        )
    if group_id is not None:
        # 完整体聚合：连同旗下小分队（后代）的关联一起命中
        stmt = stmt.where(
            Song.id.in_(
                select(SongArtistRelation.song_id).where(
                    SongArtistRelation.group_id.in_(expand_group_ids(db, group_id))
                )
            )
        )
    if album_id is not None:
        stmt = stmt.where(
            Song.id.in_(
                select(AlbumTrack.song_id).where(AlbumTrack.album_id == album_id)
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    if sort == "completion":
        stmt = stmt.order_by(completion.asc())
    elif sort == "-completion":
        stmt = stmt.order_by(completion.desc())
    else:
        stmt = stmt.order_by(Song.name)
    rows = db.execute(
        stmt.offset(pagination.offset).limit(pagination.limit)
    ).all()
    page_ids = [song.id for song, _ in rows]
    if page_ids:
        # 一枪预载本页歌曲的 album_tracks：SongRead.album_ids 是 ORM property
        # （读 album_tracks 关系），不预载会逐歌懒加载 = 每页 30 条额外 SQL。
        # selectinload 返回 identity map 里的同一批对象，property 命中已载集合。
        db.execute(
            select(Song).where(Song.id.in_(page_ids)).options(selectinload(Song.album_tracks))
        )
    counts = _song_video_counts(db, page_ids)
    names = _song_relation_names(db, page_ids)
    items = []
    for song, pct in rows:
        read = SongRead.model_validate(song)
        read.completion_pct = round(pct)
        read.video_count = counts.get(song.id, 0)
        read.relation_names = names.get(song.id) or []
        items.append(read)
    return PageResponse(
        items=items, total=total, page=pagination.page, page_size=pagination.page_size
    )


_BRIEF_LIMIT = 50
# 别名命中要走 Python 侧核对（JSON 列里中文/韩文被 ensure_ascii 转义，SQL LIKE 搜不到），
# 这里限制参与核对的候选池大小，防止将来歌多了每次搜索全表扫。
_ALIAS_SCAN = 1000
# 专辑命中最多扫多少行（防止「a」这类单字把整张 album_tracks 全捞出来）
_ALBUM_HIT_SCAN = 300


def _alias_hit(song: Song, q: str) -> Optional[str]:
    """歌曲别名里命中 q 的那条原文（忽略大小写）；没有则 None。"""
    needle = (q or "").strip().casefold()
    if not needle:
        return None
    for a in song.aliases or []:
        if isinstance(a, str) and a.strip() and needle in a.casefold():
            return a
    return None


def _name_field_hit(song: Song, q: str) -> tuple[Optional[str], Optional[str]]:
    """deep 档下这条歌靠哪个「别名类」字段命中。

    主名 / 中文名命中返回 `(None, None)`（它本来就靠名字被找到，不写提示，
    与 `/albums/brief?song_hits` 的口径一致）。
    """
    needle = (q or "").strip().casefold()
    if not needle:
        return None, None
    if needle in song.name.casefold() or needle in (song.chinese_name or "").casefold():
        return None, None
    if song.english_name and needle in song.english_name.casefold():
        return "english_name", song.english_name
    if song.korean_name and needle in song.korean_name.casefold():
        return "korean_name", song.korean_name
    return None, None


@router.get("/brief", response_model=list[SongBrief])
def list_brief(
    db: DbDep,
    q: Optional[str] = None,
    deep: bool = Query(
        False,
        description="q 额外匹配英文名 / 韩文名 / 别名（「手动找歌」用）；"
        "命中项带 matched_field / matched_value",
    ),
    with_owner: bool = Query(
        False, description="填 owner（主体展示名），供同名歌曲消歧"
    ),
    album_hits: bool = Query(
        False,
        description="q 额外匹配「所属专辑名」，命中项带 matched_album_*；"
        "只记得它收在哪张专辑时用",
    ),
):
    """歌曲精简列表（下拉 / 搜索用）。

    三个开关默认全关 —— 只按歌曲名 / 中文名匹配，AI 关联（`linkOrCreateAiRel`）
    等老调用行为**完全不变**。下拉场景建议三个都开：能按别名/韩文名搜到歌、能按
    所属专辑名搜到歌，并且每条都带主体名（库里有 I AM / Supernova / Too Hot 这类
    同名歌曲，不带主体根本分不清选哪一个）。
    """
    stmt = select(Song).where(Song.active_filter())
    like = None
    if q:
        like = f"%{q}%"
        cond = Song.name.ilike(like) | Song.chinese_name.ilike(like)
        if deep:
            cond = cond | Song.english_name.ilike(like) | Song.korean_name.ilike(like)
        stmt = stmt.where(cond)
    songs = list(db.scalars(stmt.order_by(Song.name).limit(_BRIEF_LIMIT)).all())

    by_id: dict[int, Song] = {s.id: s for s in songs}
    order: list[int] = [s.id for s in songs]
    matched_field: dict[int, str] = {}
    matched_value: dict[int, str] = {}
    matched_album: dict[int, tuple[int, str]] = {}

    if like:
        if deep:
            # 主查询里的英文名 / 韩文名命中（主名 / 中文名命中不写提示）
            for s in songs:
                field, value = _name_field_hit(s, q or "")
                if field:
                    matched_field[s.id] = field
                    matched_value[s.id] = value

        if deep and len(order) < _BRIEF_LIMIT:
            # 别名：SQL 侧不便匹配（JSON 转义），在候选池里核对原文
            pool = db.scalars(
                select(Song)
                .where(Song.active_filter(), Song.aliases.is_not(None))
                .order_by(Song.name)
                .limit(_ALIAS_SCAN)
            ).all()
            for s in pool:
                if len(order) >= _BRIEF_LIMIT:
                    break
                if s.id in by_id:
                    continue
                alias = _alias_hit(s, q or "")
                if alias is None:
                    continue
                by_id[s.id] = s
                order.append(s.id)
                matched_field[s.id] = "alias"
                matched_value[s.id] = alias

        if album_hits and len(order) < _BRIEF_LIMIT:
            rows = db.execute(
                select(Song, Album)
                .join(AlbumTrack, AlbumTrack.song_id == Song.id)
                .join(Album, Album.id == AlbumTrack.album_id)
                .where(
                    Song.deleted_at.is_(None),
                    Album.deleted_at.is_(None),
                    or_(Album.name.ilike(like), Album.chinese_name.ilike(like)),
                )
                .order_by(Album.name, Song.name)
                .limit(_ALBUM_HIT_SCAN)
            ).all()
            for song, album in rows:
                if song.id in matched_album:
                    continue
                if len(order) >= _BRIEF_LIMIT and song.id not in by_id:
                    break
                matched_album[song.id] = (album.id, album.name)
                if song.id not in by_id:
                    by_id[song.id] = song
                    order.append(song.id)

    owners = song_owner_names(db, order) if with_owner else {}
    out: list[SongBrief] = []
    for sid in order[:_BRIEF_LIMIT]:
        brief = SongBrief.model_validate(by_id[sid])
        if with_owner:
            brief.owner = owners.get(sid)
        brief.matched_field = matched_field.get(sid)
        brief.matched_value = matched_value.get(sid)
        album = matched_album.get(sid)
        if album is not None:
            brief.matched_album_id, brief.matched_album_name = album
        out.append(brief)
    return out


@router.get("/fuzzy", response_model=list[SongFuzzyHit])
def fuzzy_song_names(db: DbDep, q: Optional[str] = Query(None)):
    """按归一化曲名模糊匹配歌曲（忽略空格/大小写，含各语言名与别名）。"""
    hits = fuzzy_songs(db, q or "")
    return [
        SongFuzzyHit(
            id=s.id,
            name=s.name,
            chinese_name=s.chinese_name,
            english_name=s.english_name,
            korean_name=s.korean_name,
            score=round(score, 4),
        )
        for s, score in hits
    ]


@router.get("/by-uid/{uid}", response_model=SongRead)
def get_song_by_uid(uid: str, db: DbDep):
    """按永久业务 uid 查询；已软删除的实体默认不可见。"""
    song = Song.get_by_uid(db, uid)
    if not song:
        raise HTTPException(404, "Song 不存在")
    read = SongRead.model_validate(song)
    read.video_count = _song_video_counts(db, [song.id]).get(song.id, 0)
    read.relation_names = _song_relation_names(db, [song.id]).get(song.id) or []
    return read


@router.get("/{song_id}/albums", response_model=list[AlbumBrief])
def list_song_albums(song_id: int, db: DbDep):
    """返回某首歌曲关联的所有专辑（通过 AlbumTrack 推导），已软删除的专辑不返回。"""
    if not Song.get_active(db, song_id):
        raise HTTPException(404, "Song 不存在或已删除")
    return db.scalars(
        select(Album)
        .join(AlbumTrack, AlbumTrack.album_id == Album.id)
        .where(AlbumTrack.song_id == song_id, Album.deleted_at.is_(None))
        .order_by(Album.release_date.desc().nulls_last())
        .limit(20)
    ).all()


@router.post("", response_model=SongRead, status_code=201)
def create_song(payload: SongCreate, db: DbDep):
    s = Song(**payload.model_dump())
    validate_release_artist(db, s.release_artist_type, s.release_artist_id)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.get("/{song_id}", response_model=SongRead)
def get_song(song_id: int, db: DbDep):
    song = Song.get_active(db, song_id)
    if not song:
        raise HTTPException(404, "Song 不存在")
    read = SongRead.model_validate(song)
    read.video_count = _song_video_counts(db, [song.id]).get(song.id, 0)
    read.relation_names = _song_relation_names(db, [song.id]).get(song.id) or []
    return read


def _sync_song_albums(db, song_id: int, album_ids: list[int]) -> None:
    album_ids = list(dict.fromkeys(album_ids))
    valid_ids = set(
        db.scalars(
            select(Album.id).where(Album.id.in_(album_ids), Album.deleted_at.is_(None))
        ).all()
    )
    missing = set(album_ids) - valid_ids
    if missing:
        raise HTTPException(400, f"专辑不存在或已删除: {sorted(missing)}")
    existing = {
        t.album_id: t
        for t in db.scalars(select(AlbumTrack).where(AlbumTrack.song_id == song_id)).all()
    }
    want = set(album_ids)
    for aid, row in existing.items():
        if aid not in want:
            db.delete(row)
    db.flush()
    for album_id in album_ids:
        if album_id in existing:
            continue
        max_tn = (
            db.scalar(
                select(func.max(AlbumTrack.track_number)).where(
                    AlbumTrack.album_id == album_id
                )
            )
            or 0
        )
        db.add(
            AlbumTrack(
                album_id=album_id,
                song_id=song_id,
                disc_number=1,
                track_number=max_tn + 1,
            )
        )


@router.patch("/{song_id}", response_model=SongRead)
def update_song(song_id: int, payload: SongUpdate, db: DbDep):
    song = Song.get_active(db, song_id)
    if not song:
        raise HTTPException(404, "Song 不存在")
    data = payload.model_dump(exclude_unset=True)
    album_ids = data.pop("album_ids", None)
    for k, v in data.items():
        setattr(song, k, v)
    validate_release_artist(db, song.release_artist_type, song.release_artist_id)
    if album_ids is not None:
        _sync_song_albums(db, song_id, album_ids)
    try:
        db.commit()
    except IntegrityError as e:
        # 并发写入同一专辑时 max(track_number)+1 可能撞 uq_album_track_position
        db.rollback()
        raise HTTPException(409, "专辑曲目位置冲突（并发修改），请重试") from e
    db.refresh(song)
    return song


@router.delete("/{song_id}", status_code=204)
def delete_song(song_id: int, db: DbDep):
    """软删除歌曲：写入 deleted_at 墓碑（不级联拆演唱关系）。"""
    song = Song.get_active(db, song_id)
    if not song:
        raise HTTPException(404, "Song 不存在")
    soft_delete_song(db, song)
    db.commit()


# ===== SongArtistRelation =====
@router.get("/{song_id}/relations", response_model=List[SongArtistRelationRead], tags=["song-relations"])
def list_relations(song_id: int, db: DbDep):
    if not Song.get_active(db, song_id):
        raise HTTPException(404, "Song 不存在或已删除")
    return (
        db.scalars(
            select(SongArtistRelation)
            .outerjoin(Artist, SongArtistRelation.artist_id == Artist.id)
            .outerjoin(Group, SongArtistRelation.group_id == Group.id)
            .where(
                SongArtistRelation.song_id == song_id,
                or_(
                    and_(
                        SongArtistRelation.artist_id.is_not(None),
                        Artist.deleted_at.is_(None),
                    ),
                    and_(
                        SongArtistRelation.group_id.is_not(None),
                        Group.deleted_at.is_(None),
                    ),
                ),
            )
            .order_by(SongArtistRelation.order)
        )
        .all()
    )


@router.post(
    "/relations",
    response_model=SongArtistRelationRead,
    tags=["song-relations"],
    status_code=201,
)
def create_relation(payload: SongArtistRelationCreate, db: DbDep):
    if bool(payload.artist_id) == bool(payload.group_id):
        raise HTTPException(400, "artist_id 与 group_id 必须恰有一个非空")
    require_active(db, Song, payload.song_id, "Song")
    if payload.artist_id is not None:
        require_active(db, Artist, payload.artist_id, "Artist")
    if payload.group_id is not None:
        require_active(db, Group, payload.group_id, "Group")
    r = SongArtistRelation(**payload.model_dump())
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


@router.delete("/relations/{relation_id}", status_code=204, tags=["song-relations"])
def delete_relation(relation_id: int, db: DbDep):
    r = db.get(SongArtistRelation, relation_id)
    if not r:
        raise HTTPException(404, "Relation 不存在")
    db.delete(r)
    db.commit()


# ===== Credits =====
@router.get("/{song_id}/credits", response_model=List[CreditsRead], tags=["song-credits"])
def list_credits(song_id: int, db: DbDep):
    if not Song.get_active(db, song_id):
        raise HTTPException(404, "Song 不存在或已删除")
    rows = db.execute(
        select(Credits, Artist)
        .outerjoin(Artist, Credits.artist_id == Artist.id)
        .where(Credits.song_id == song_id)
        .order_by(Credits.role)
    ).all()
    return [
        c
        for c, a in rows
        if c.artist_id is None or (a is not None and a.deleted_at is None)
    ]


@router.post(
    "/credits", response_model=CreditsRead, tags=["song-credits"], status_code=201
)
def create_credit(payload: CreditsCreate, db: DbDep):
    require_active(db, Song, payload.song_id, "Song")
    if payload.artist_id is not None:
        require_active(db, Artist, payload.artist_id, "Artist")
    c = Credits(**payload.model_dump())
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@router.delete("/credits/{credit_id}", status_code=204, tags=["song-credits"])
def delete_credit(credit_id: int, db: DbDep):
    c = db.get(Credits, credit_id)
    if not c:
        raise HTTPException(404, "Credit 不存在")
    db.delete(c)
    db.commit()
