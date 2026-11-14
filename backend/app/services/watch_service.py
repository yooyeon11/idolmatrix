"""观看进度上报与观看排行。

一次连续播放（同一视频 10 分钟内再次上报）合并成一行 WatchPlay。
拖进度条只按墙钟累计时长，避免 seek 把观看秒数撑爆。
**有效播放 = 完播，或累计观看 ≥ min(15 秒, 时长 × 10%)**（2026-10-28 业主口径）；
不足阈值的记录不进任何排行/统计。建行与计次是两件事：整次播放的第一帧就会建行，
但只有达到阈值才算「一次播放」——所有出口都必须走 qualified_play_clause()。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import and_, case, desc, func, or_, select, union
from sqlalchemy.orm import Session

from app.models.artist import Artist
from app.models.group import Group
from app.models.music_video import (
    MusicVideo,
    MusicVideoTrack,
    music_video_artists,
    music_video_groups,
)
from app.models.song import Song
from app.models.watch import WatchPlay
from app.services.stats_service import DEFAULT_LIMIT, MAX_LIMIT, _rank_item

RESUME_WINDOW_SECONDS = 600.0
MAX_TICK_SECONDS = 45.0
COMPLETE_RATIO = 0.9

# 「一次播放」的有效阈值 = min(15 秒, 时长 × 10%)：
# 3 分钟打歌舞台按 15 秒封顶；60 秒短片按 6 秒；30 秒按 3 秒。时长未知/异常时退回 15 秒。
MIN_PLAY_SECONDS = 15.0
MIN_PLAY_RATIO = 0.10


def _now() -> datetime:
    return datetime.utcnow()


def min_play_seconds(duration: Optional[float]) -> float:
    """有效播放阈值（秒）：min(MIN_PLAY_SECONDS, 时长 × MIN_PLAY_RATIO)。

    时长缺失或非正数时不能用比例（会退化成 0 秒，任何残留行都算一次播放），
    直接退回 15 秒上限。
    """
    d = float(duration or 0.0)
    if d <= 0:
        return MIN_PLAY_SECONDS
    return min(MIN_PLAY_SECONDS, d * MIN_PLAY_RATIO)


def _qualify():
    """有效播放判定（SQL 条件）：完播，或累计观看达到该行的时长阈值。"""
    ratio = WatchPlay.source_duration * MIN_PLAY_RATIO
    threshold = case(
        (
            or_(
                WatchPlay.source_duration.is_(None),
                WatchPlay.source_duration <= 0,
            ),
            MIN_PLAY_SECONDS,
        ),
        (ratio < MIN_PLAY_SECONDS, ratio),
        else_=MIN_PLAY_SECONDS,
    )
    return or_(
        WatchPlay.completed.is_(True),
        WatchPlay.seconds_watched >= threshold,
    )


def qualified_play_clause():
    """对外暴露的有效播放条件。

    热度榜（stats_service._heat_rows）等其它出口**必须**用这个，而不是裸数行数，
    否则 0 秒的误触记录会被算成播放，和观看榜对不上。
    """
    return _qualify()


def record_watch(
    db: Session,
    *,
    music_video_id: int,
    position: float = 0.0,
    duration: Optional[float] = None,
    playing: bool = False,
    ended: bool = False,
) -> dict[str, Any]:
    mv = db.get(MusicVideo, music_video_id)
    if mv is None or mv.deleted_at is not None:
        raise HTTPException(status_code=404, detail="视频不存在")

    now = _now()
    position = max(0.0, float(position or 0.0))
    duration = float(duration or 0.0) or float(mv.duration or 0) or 0.0

    play = db.scalar(
        select(WatchPlay)
        .where(
            WatchPlay.music_video_id == music_video_id,
            WatchPlay.last_seen_at >= now - timedelta(seconds=RESUME_WINDOW_SECONDS),
        )
        .order_by(WatchPlay.last_seen_at.desc())
        .limit(1)
    )
    if play is None:
        play = WatchPlay(
            music_video_id=music_video_id,
            started_at=now,
            last_seen_at=now,
            last_position=position,
            max_position=position,
            seconds_watched=0.0,
            source_duration=duration or None,
            completed=False,
        )
        db.add(play)
    else:
        wall = max(0.0, (now - play.last_seen_at).total_seconds())
        last_pos = float(play.last_position or 0.0)
        pos_delta = position - last_pos
        added = 0.0
        if playing or ended:
            if pos_delta >= -0.5:
                # 正常播放：墙钟与进度增量取较小值，挡住 seek 前跳
                if pos_delta > 0.05:
                    added = min(wall, pos_delta, MAX_TICK_SECONDS)
                elif wall > 0:
                    added = min(wall, MAX_TICK_SECONDS)
            play.seconds_watched = float(play.seconds_watched or 0.0) + added
        play.last_seen_at = now
        play.last_position = position
        play.max_position = max(float(play.max_position or 0.0), position)
        if duration:
            play.source_duration = duration

    dur = float(play.source_duration or duration or 0.0)
    if ended or (
        dur > 1
        and (
            float(play.max_position or 0.0) >= dur * COMPLETE_RATIO
            or float(play.seconds_watched or 0.0) >= dur * COMPLETE_RATIO
        )
    ):
        play.completed = True

    db.commit()
    db.refresh(play)
    return {
        "play_id": play.id,
        "music_video_id": play.music_video_id,
        "seconds_watched": round(float(play.seconds_watched or 0.0), 2),
        "completed": bool(play.completed),
    }


def _watch_base(*, range_key: str, include_shorts: bool):
    conds = [_qualify()]
    if range_key == "30d":
        conds.append(WatchPlay.started_at >= _now() - timedelta(days=30))
    if not include_shorts:
        conds.append(
            or_(MusicVideo.is_short.is_(False), MusicVideo.is_short.is_(None))
        )
    return and_(*conds)


def watch_overview(
    db: Session, *, range_key: str, include_shorts: bool
) -> dict[str, Any]:
    vf = _watch_base(range_key=range_key, include_shorts=include_shorts)
    stmt = (
        select(
            func.count(WatchPlay.id),
            func.coalesce(func.sum(WatchPlay.seconds_watched), 0),
            func.coalesce(
                func.sum(case((WatchPlay.completed.is_(True), 1), else_=0)),
                0,
            ),
        )
        .select_from(WatchPlay)
        .join(MusicVideo, MusicVideo.id == WatchPlay.music_video_id)
        .where(vf, MusicVideo.deleted_at.is_(None))
    )
    row = db.execute(stmt).one()
    return {
        "play_count": int(row[0] or 0),
        "seconds_watched": int(row[1] or 0),
        "completed_count": int(row[2] or 0),
    }


def rank_watch_artists(
    db: Session, *, range_key: str, include_shorts: bool, limit: int
) -> list[dict[str, Any]]:
    vf = _watch_base(range_key=range_key, include_shorts=include_shorts)
    assoc = select(
        music_video_artists.c.artist_id.label("artist_id"),
        music_video_artists.c.music_video_id.label("video_id"),
    )
    subject = select(
        MusicVideo.subject_artist_id.label("artist_id"),
        MusicVideo.id.label("video_id"),
    ).where(MusicVideo.subject_artist_id.is_not(None))
    links = union(assoc, subject).subquery("watch_artist_links")
    stmt = (
        select(
            Artist.id,
            Artist.uid,
            Artist.name,
            Artist.chinese_name,
            Artist.stage_name,
            Artist.avatar_path,
            func.count(func.distinct(WatchPlay.id)).label("video_count"),
            func.coalesce(func.sum(WatchPlay.seconds_watched), 0).label(
                "duration_seconds"
            ),
        )
        .select_from(WatchPlay)
        .join(MusicVideo, MusicVideo.id == WatchPlay.music_video_id)
        .join(links, links.c.video_id == MusicVideo.id)
        .join(Artist, Artist.id == links.c.artist_id)
        .where(vf, MusicVideo.deleted_at.is_(None), Artist.deleted_at.is_(None))
        .group_by(Artist.id)
        .order_by(desc("duration_seconds"), desc("video_count"), Artist.name.asc())
        .limit(limit)
    )
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
        for r in db.execute(stmt).all()
    ]


def rank_watch_groups(
    db: Session, *, range_key: str, include_shorts: bool, limit: int
) -> list[dict[str, Any]]:
    vf = _watch_base(range_key=range_key, include_shorts=include_shorts)
    stmt = (
        select(
            Group.id,
            Group.uid,
            Group.name,
            Group.chinese_name,
            Group.group_type,
            Group.avatar_path,
            func.count(func.distinct(WatchPlay.id)).label("video_count"),
            func.coalesce(func.sum(WatchPlay.seconds_watched), 0).label(
                "duration_seconds"
            ),
        )
        .select_from(WatchPlay)
        .join(MusicVideo, MusicVideo.id == WatchPlay.music_video_id)
        .join(
            music_video_groups,
            music_video_groups.c.music_video_id == MusicVideo.id,
        )
        .join(Group, Group.id == music_video_groups.c.group_id)
        .where(vf, MusicVideo.deleted_at.is_(None), Group.deleted_at.is_(None))
        .group_by(Group.id)
        .order_by(desc("duration_seconds"), desc("video_count"), Group.name.asc())
        .limit(limit)
    )
    return [
        _rank_item(
            id=r.id,
            uid=r.uid,
            name=r.name,
            chinese_name=r.chinese_name,
            extra=r.group_type,
            avatar_path=r.avatar_path,
            video_count=r.video_count,
            duration_seconds=r.duration_seconds,
        )
        for r in db.execute(stmt).all()
    ]


def rank_watch_songs(
    db: Session, *, range_key: str, include_shorts: bool, limit: int
) -> list[dict[str, Any]]:
    vf = _watch_base(range_key=range_key, include_shorts=include_shorts)
    stmt = (
        select(
            Song.id,
            Song.uid,
            Song.name,
            Song.chinese_name,
            Song.song_type,
            func.count(func.distinct(WatchPlay.id)).label("video_count"),
            func.coalesce(func.sum(WatchPlay.seconds_watched), 0).label(
                "duration_seconds"
            ),
        )
        .select_from(WatchPlay)
        .join(MusicVideo, MusicVideo.id == WatchPlay.music_video_id)
        .join(MusicVideoTrack, MusicVideoTrack.music_video_id == MusicVideo.id)
        .join(Song, Song.id == MusicVideoTrack.song_id)
        .where(vf, MusicVideo.deleted_at.is_(None), Song.deleted_at.is_(None))
        .group_by(Song.id)
        .order_by(desc("duration_seconds"), desc("video_count"), Song.name.asc())
        .limit(limit)
    )
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
        for r in db.execute(stmt).all()
    ]


def rank_watch_videos(
    db: Session, *, range_key: str, include_shorts: bool, limit: int
) -> list[dict[str, Any]]:
    vf = _watch_base(range_key=range_key, include_shorts=include_shorts)
    stmt = (
        select(
            MusicVideo.id,
            MusicVideo.uid,
            MusicVideo.name,
            MusicVideo.video_type,
            func.count(WatchPlay.id).label("video_count"),
            func.coalesce(func.sum(WatchPlay.seconds_watched), 0).label(
                "duration_seconds"
            ),
        )
        .select_from(WatchPlay)
        .join(MusicVideo, MusicVideo.id == WatchPlay.music_video_id)
        .where(vf, MusicVideo.deleted_at.is_(None))
        .group_by(MusicVideo.id)
        .order_by(desc("duration_seconds"), desc("video_count"), MusicVideo.name.asc())
        .limit(limit)
    )
    return [
        _rank_item(
            id=r.id,
            uid=r.uid,
            name=r.name,
            chinese_name=None,
            extra=r.video_type,
            video_count=r.video_count,
            duration_seconds=r.duration_seconds,
        )
        for r in db.execute(stmt).all()
    ]


def watch_block(
    db: Session, *, range_key: str, include_shorts: bool, limit: int
) -> dict[str, Any]:
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    ov = watch_overview(db, range_key=range_key, include_shorts=include_shorts)
    return {
        "available": True,
        "play_count": ov["play_count"],
        "seconds_watched": ov["seconds_watched"],
        "completed_count": ov["completed_count"],
        "artists": rank_watch_artists(
            db, range_key=range_key, include_shorts=include_shorts, limit=limit
        ),
        "groups": rank_watch_groups(
            db, range_key=range_key, include_shorts=include_shorts, limit=limit
        ),
        "songs": rank_watch_songs(
            db, range_key=range_key, include_shorts=include_shorts, limit=limit
        ),
        "videos": rank_watch_videos(
            db, range_key=range_key, include_shorts=include_shorts, limit=limit
        ),
        "items": rank_watch_videos(
            db, range_key=range_key, include_shorts=include_shorts, limit=limit
        ),
    }
