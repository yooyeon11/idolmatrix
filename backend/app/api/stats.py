"""馆藏统计、观看上报与观看排行。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.deps import DbDep
from app.schemas.dashboard import (
    LibraryStats,
    StatsRecap,
    WatchPingRequest,
    WatchPingResult,
)
from app.services.stats_service import DEFAULT_LIMIT, MAX_LIMIT, library_stats, recap
from app.services.stats_service import artist_trend
from app.services.watch_service import record_watch

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("", response_model=LibraryStats)
def get_library_stats(
    db: DbDep,
    range: str = Query("all", description="all=全部，30d=近 30 天，year=当年（库内按入库，观看按开播时间）"),
    include_shorts: bool = Query(False, description="是否把短视频计入排行"),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
):
    return library_stats(
        db, range_key=range, include_shorts=include_shorts, limit=limit
    )


@router.get("/artist-trend")
def get_artist_trend(
    db: DbDep,
    by: str = Query("created", description="created=按入库时间，performance=按表演时间"),
    months: int = Query(12, ge=3, le=36),
    limit: int = Query(5, ge=1, le=10),
):
    """艺人轨迹（实验性）：主体 × 月份入库条数，堆叠柱与热度榜共用。"""
    return artist_trend(db, by=by, months=months, limit=limit)


@router.get("/recap", response_model=StatsRecap)
def get_recap(db: DbDep):
    """年度回顾补充数据：入库热力日历 / 观看时钟 / 馆藏之最 / 月度趋势。"""
    return recap(db)


@router.post("/watch", response_model=WatchPingResult)
def ping_watch(payload: WatchPingRequest, db: DbDep):
    """播放器进度心跳：合并成一次连续观看，供统计页排行。"""
    return record_watch(
        db,
        music_video_id=payload.music_video_id,
        position=payload.position,
        duration=payload.duration,
        playing=payload.playing,
        ended=payload.ended,
    )
