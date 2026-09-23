"""馆藏统计与库内排行。观看排行由 watch_service 根据 WatchPlay 汇总。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import and_, desc, func, or_, select, union
from sqlalchemy.orm import Session

from app.models.artist import Artist
from app.models.group import Group
from app.models.incoming_file import IncomingFile
from app.models.music_video import (
    MusicVideo,
    MusicVideoTrack,
    music_video_artists,
    music_video_groups,
)
from app.models.photo import Photo
from app.models.song import Song
from app.models.watch import WatchPlay
from app.services.group_tree import family_group_ids
from app.services.video_meta import (
    RESOLUTION_TIERS,
    RESOLUTION_UNKNOWN,
    resolution_tier,
)

DEFAULT_LIMIT = 20
MAX_LIMIT = 50


def _cutoff_30d() -> datetime:
    return datetime.utcnow() - timedelta(days=30)


def _library_video_filter(*, range_key: str, include_shorts: bool):
    conds = [
        MusicVideo.deleted_at.is_(None),
        MusicVideo.ingestion_status == "library",
    ]
    if not include_shorts:
        conds.append(
            or_(MusicVideo.is_short.is_(False), MusicVideo.is_short.is_(None))
        )
    if range_key == "30d":
        conds.append(MusicVideo.created_at >= _cutoff_30d())
    if range_key == "year":
        # 真·当年口径：按容器本地时区（recap 同款 localtime 修饰符）的 1 月 1 日起
        year_start = f"{_utcnow().year:04d}-01-01"
        conds.append(func.datetime(MusicVideo.created_at, "localtime") >= year_start)
    return and_(*conds)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _rank_item(
    *,
    id: int,
    uid: str,
    name: str,
    chinese_name: Optional[str],
    extra: Optional[str],
    video_count: int,
    duration_seconds: int,
    avatar_path: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "uid": uid,
        "name": name,
        "chinese_name": chinese_name,
        "extra": extra,
        "avatar_path": avatar_path,
        "video_count": int(video_count or 0),
        "duration_seconds": int(duration_seconds or 0),
    }


def overview(db: Session) -> dict[str, Any]:
    active = MusicVideo.deleted_at.is_(None)
    library = and_(active, MusicVideo.ingestion_status == "library")
    shorts = and_(
        library,
        MusicVideo.is_short.is_(True),
    )
    last_30d = and_(library, MusicVideo.created_at >= _cutoff_30d())

    library_count = db.scalar(select(func.count(MusicVideo.id)).where(library)) or 0
    incoming_count = db.scalar(select(func.count(IncomingFile.id))) or 0
    shorts_count = db.scalar(select(func.count(MusicVideo.id)).where(shorts)) or 0
    last_30d_count = db.scalar(select(func.count(MusicVideo.id)).where(last_30d)) or 0
    total_duration = (
        db.scalar(select(func.coalesce(func.sum(MusicVideo.duration), 0)).where(library))
        or 0
    )
    total_size = (
        db.scalar(select(func.coalesce(func.sum(MusicVideo.file_size), 0)).where(library))
        or 0
    )
    photo_count = (
        db.scalar(select(func.count(Photo.id)).where(Photo.deleted_at.is_(None))) or 0
    )

    rows = db.execute(
        select(MusicVideo.video_type, func.count(MusicVideo.id))
        .where(library)
        .group_by(MusicVideo.video_type)
    ).all()
    type_breakdown = {vt or "Other": int(cnt) for vt, cnt in rows}

    # 分辨率构成：按 (width,height) 分组后在 Python 侧归档，档位少、DB 侧不用写 CASE
    res_rows = db.execute(
        select(MusicVideo.width, MusicVideo.height, func.count(MusicVideo.id))
        .where(library)
        .group_by(MusicVideo.width, MusicVideo.height)
    ).all()
    res_counts: dict[str, int] = {}
    for w, h, cnt in res_rows:
        key = resolution_tier(w, h)
        res_counts[key] = res_counts.get(key, 0) + int(cnt)
    ordered = [key for key, _floor in RESOLUTION_TIERS if key in res_counts]
    if RESOLUTION_UNKNOWN in res_counts:
        ordered.append(RESOLUTION_UNKNOWN)
    resolution_breakdown = {key: res_counts[key] for key in ordered}

    return {
        "library_count": int(library_count),
        "incoming_count": int(incoming_count),
        "shorts_count": int(shorts_count),
        "last_30d_count": int(last_30d_count),
        "total_duration_seconds": int(total_duration),
        "total_size_bytes": int(total_size),
        "photo_count": int(photo_count),
        "type_breakdown": type_breakdown,
        "resolution_breakdown": resolution_breakdown,
    }


def rank_artists(
    db: Session, *, range_key: str, include_shorts: bool, limit: int
) -> list[dict[str, Any]]:
    vf = _library_video_filter(range_key=range_key, include_shorts=include_shorts)
    assoc = select(
        music_video_artists.c.artist_id.label("artist_id"),
        music_video_artists.c.music_video_id.label("video_id"),
    )
    subject = select(
        MusicVideo.subject_artist_id.label("artist_id"),
        MusicVideo.id.label("video_id"),
    ).where(MusicVideo.subject_artist_id.is_not(None))
    links = union(assoc, subject).subquery("artist_video_links")
    stmt = (
        select(
            Artist.id,
            Artist.uid,
            Artist.name,
            Artist.chinese_name,
            Artist.stage_name,
            Artist.avatar_path,
            func.count(func.distinct(links.c.video_id)).label("video_count"),
            func.coalesce(func.sum(MusicVideo.duration), 0).label("duration_seconds"),
        )
        .select_from(links)
        .join(MusicVideo, MusicVideo.id == links.c.video_id)
        .join(Artist, Artist.id == links.c.artist_id)
        .where(vf, Artist.deleted_at.is_(None))
        .group_by(Artist.id)
        .order_by(desc("video_count"), desc("duration_seconds"), Artist.name.asc())
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    return [
        _rank_item(
            id=r.id,
            uid=r.uid,
            name=r.name,
            chinese_name=r.chinese_name,
            extra=r.stage_name,
            avatar_path=r.avatar_path,
            video_count=r.video_count,
            duration_seconds=r.duration_seconds,
        )
        for r in rows
    ]


def rank_subject_artists(
    db: Session, *, range_key: str, include_shorts: bool, limit: int
) -> list[dict[str, Any]]:
    """镜头焦点：仅按「直拍对象」（subject_artist_id）聚合，不混入参演关联。

    与 rank_artists（参演 ∪ 直拍对象）口径互补：反映"你把镜头对准了谁"。
    """
    vf = _library_video_filter(range_key=range_key, include_shorts=include_shorts)
    stmt = (
        select(
            Artist.id,
            Artist.uid,
            Artist.name,
            Artist.chinese_name,
            Artist.stage_name,
            Artist.avatar_path,
            func.count(func.distinct(MusicVideo.id)).label("video_count"),
            func.coalesce(func.sum(MusicVideo.duration), 0).label("duration_seconds"),
        )
        .select_from(MusicVideo)
        .join(Artist, Artist.id == MusicVideo.subject_artist_id)
        .where(vf, Artist.deleted_at.is_(None))
        .group_by(Artist.id)
        .order_by(desc("video_count"), desc("duration_seconds"), Artist.name.asc())
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    return [
        _rank_item(
            id=r.id,
            uid=r.uid,
            name=r.name,
            chinese_name=r.chinese_name,
            extra=r.stage_name,
            avatar_path=r.avatar_path,
            video_count=r.video_count,
            duration_seconds=r.duration_seconds,
        )
        for r in rows
    ]


def rank_groups(
    db: Session, *, range_key: str, include_shorts: bool, limit: int
) -> list[dict[str, Any]]:
    """组合排行：「母队口径含小分队」。

    分组在 SQL 里做不了（一个组合的家族是它 + 全部后代），所以取
    (组合 → 视频, 时长) 明细后在 Python 侧按家族并集去重再排序 ——
    与视频列表 / 组合列表同一个 `family_group_ids` 口径。
    小分队自身仍单列（数字同样含其下级），便于看出哪支产量高。
    """
    vf = _library_video_filter(range_key=range_key, include_shorts=include_shorts)
    groups = db.scalars(select(Group).where(Group.deleted_at.is_(None))).all()
    if not groups:
        return []
    by_id = {g.id: g for g in groups}
    family = family_group_ids(db, list(by_id))
    family_ids = {gid for members in family.values() for gid in members}

    rows = db.execute(
        select(
            music_video_groups.c.group_id,
            MusicVideo.id,
            MusicVideo.duration,
        )
        .select_from(music_video_groups)
        .join(MusicVideo, MusicVideo.id == music_video_groups.c.music_video_id)
        .where(vf, music_video_groups.c.group_id.in_(family_ids))
    ).all()
    videos_by_group: dict[int, dict[int, float]] = {}
    for gid, video_id, duration in rows:
        videos_by_group.setdefault(gid, {})[video_id] = float(duration or 0)

    scored: list[tuple[Group, int, float]] = []
    for gid, g in by_id.items():
        seen: dict[int, float] = {}
        for member in family.get(gid) or {gid}:
            seen.update(videos_by_group.get(member, {}))
        if seen:
            scored.append((g, len(seen), sum(seen.values())))
    scored.sort(key=lambda t: (-t[1], -t[2], t[0].name or ""))
    return [
        _rank_item(
            id=g.id,
            uid=g.uid,
            name=g.name,
            chinese_name=g.chinese_name,
            extra=g.group_type,
            avatar_path=g.avatar_path,
            video_count=n,
            duration_seconds=int(dur),
        )
        for g, n, dur in scored[:limit]
    ]


def rank_songs(
    db: Session, *, range_key: str, include_shorts: bool, limit: int
) -> list[dict[str, Any]]:
    vf = _library_video_filter(range_key=range_key, include_shorts=include_shorts)
    stmt = (
        select(
            Song.id,
            Song.uid,
            Song.name,
            Song.chinese_name,
            Song.song_type,
            func.count(func.distinct(MusicVideo.id)).label("video_count"),
            func.coalesce(func.sum(MusicVideo.duration), 0).label("duration_seconds"),
        )
        .select_from(MusicVideoTrack)
        .join(MusicVideo, MusicVideo.id == MusicVideoTrack.music_video_id)
        .join(Song, Song.id == MusicVideoTrack.song_id)
        .where(vf, Song.deleted_at.is_(None))
        .group_by(Song.id)
        .order_by(desc("video_count"), desc("duration_seconds"), Song.name.asc())
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    return [
        _rank_item(
            id=r.id,
            uid=r.uid,
            name=r.name,
            chinese_name=r.chinese_name,
            extra=r.song_type,
            video_count=r.video_count,
            duration_seconds=r.duration_seconds,
        )
        for r in rows
    ]


def library_stats(
    db: Session, *, range_key: str = "all", include_shorts: bool = False, limit: int = DEFAULT_LIMIT
) -> dict[str, Any]:
    if range_key not in ("all", "30d", "year"):
        range_key = "all"
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    return {
        "overview": overview(db),
        "range": range_key,
        "include_shorts": include_shorts,
        "artists": rank_artists(
            db, range_key=range_key, include_shorts=include_shorts, limit=limit
        ),
        "groups": rank_groups(
            db, range_key=range_key, include_shorts=include_shorts, limit=limit
        ),
        "subject_artists": rank_subject_artists(
            db, range_key=range_key, include_shorts=include_shorts, limit=limit
        ),
        "songs": rank_songs(
            db, range_key=range_key, include_shorts=include_shorts, limit=limit
        ),
        "watch": _watch_block(
            db, range_key=range_key, include_shorts=include_shorts, limit=limit
        ),
    }


def _watch_block(
    db: Session, *, range_key: str, include_shorts: bool, limit: int
) -> dict[str, Any]:
    from app.services.watch_service import watch_block

    return watch_block(
        db, range_key=range_key, include_shorts=include_shorts, limit=limit
    )


# ===== 年度回顾（recap）：热力日历 / 观看时钟 / 馆藏之最 / 月度趋势 =====
# 时间字段按 SQLite 'localtime' 修饰符转本地时区聚合（容器 TZ=Asia/Shanghai），
# 与统计页「你在几点看」的直觉一致。


def _recap_record(row) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return {
        "id": row.id,
        "uid": row.uid,
        "name": row.name,
        "chinese_name": row.chinese_name,
        "video_type": row.video_type,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "duration": int(row.duration or 0),
        "file_size": int(row.file_size or 0),
    }


_RECORD_COLS = (
    MusicVideo.id,
    MusicVideo.uid,
    MusicVideo.name,
    MusicVideo.chinese_name,
    MusicVideo.video_type,
    MusicVideo.created_at,
    MusicVideo.duration,
    MusicVideo.file_size,
)


def recap(db: Session) -> dict[str, Any]:
    """统计页「年度回顾」补充数据，全部由现有字段 GROUP BY 聚合。"""
    library = and_(
        MusicVideo.deleted_at.is_(None),
        MusicVideo.ingestion_status == "library",
    )

    # ---- 1. 入库热力日历：近 365 天逐日入库量 ----
    today = datetime.now().date()
    start = today - timedelta(days=364)
    rows = db.execute(
        select(
            func.date(MusicVideo.created_at, "localtime").label("d"),
            func.count(MusicVideo.id).label("c"),
        )
        .where(library, func.date(MusicVideo.created_at, "localtime") >= start.isoformat())
        .group_by("d")
    ).all()
    daily = {str(r.d): int(r.c) for r in rows}
    heatmap = [
        {"date": (start + timedelta(days=i)).isoformat(), "count": daily.get((start + timedelta(days=i)).isoformat(), 0)}
        for i in range(365)
    ]

    # ---- 2. 观看时钟：24 小时观看次数分布（含短视频观看） ----
    clock_rows = db.execute(
        select(
            func.strftime("%H", WatchPlay.started_at, "localtime").label("h"),
            func.count(WatchPlay.id).label("c"),
        ).group_by("h")
    ).all()
    clock = [0] * 24
    for r in clock_rows:
        h = int(r.h)
        if 0 <= h < 24:
            clock[h] = int(r.c)
    peak_hour = clock.index(max(clock)) if any(clock) else None

    # ---- 3. 馆藏之最：第一支 / 时长最长 / 体积最大 ----
    first = _recap_record(
        db.execute(
            select(*_RECORD_COLS)
            .where(library)
            .order_by(MusicVideo.created_at.asc(), MusicVideo.id.asc())
            .limit(1)
        ).first()
    )
    longest = _recap_record(
        db.execute(
            select(*_RECORD_COLS)
            .where(library, MusicVideo.duration.is_not(None), MusicVideo.duration > 0)
            .order_by(MusicVideo.duration.desc())
            .limit(1)
        ).first()
    )
    largest = _recap_record(
        db.execute(
            select(*_RECORD_COLS)
            .where(library, MusicVideo.file_size.is_not(None), MusicVideo.file_size > 0)
            .order_by(MusicVideo.file_size.desc())
            .limit(1)
        ).first()
    )

    # ---- 4. 月度入库趋势：近 12 个月 ----
    now = datetime.now()
    y, m = now.year, now.month - 11
    if m <= 0:
        m += 12
        y -= 1
    months: list[str] = []
    for _ in range(12):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    month_rows = db.execute(
        select(
            func.strftime("%Y-%m", MusicVideo.created_at, "localtime").label("m"),
            func.count(MusicVideo.id).label("c"),
        )
        .where(library, func.strftime("%Y-%m", MusicVideo.created_at, "localtime") >= months[0])
        .group_by("m")
    ).all()
    monthly_map = {str(r.m): int(r.c) for r in month_rows}
    monthly = [{"month": mk, "count": monthly_map.get(mk, 0)} for mk in months]

    return {
        "heatmap": heatmap,
        "clock": [{"hour": h, "count": c} for h, c in enumerate(clock)],
        "clock_peak_hour": peak_hour,
        "records": {"first": first, "longest": longest, "largest": largest},
        "monthly": monthly,
    }
