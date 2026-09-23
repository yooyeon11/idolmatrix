"""Dashboard 路由：数量统计。"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from app.core.deps import DbDep
from app.models.artist import Artist
from app.models.group import Group
from app.models.album import Album
from app.models.incoming_file import IncomingFile
from app.models.music_video import MusicVideo
from app.models.song import Song
from app.schemas import DashboardStats
from app.services.library_service import maybe_cleanup_missing_videos

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStats)
def get_stats(db: DbDep):
    maybe_cleanup_missing_videos(db)
    active = MusicVideo.deleted_at.is_(None)
    total_videos = db.scalar(
        select(func.count(MusicVideo.id)).where(active)
    ) or 0
    incoming = db.scalar(select(func.count(IncomingFile.id))) or 0
    library = db.scalar(
        select(func.count(MusicVideo.id)).where(
            active, MusicVideo.ingestion_status == "library"
        )
    ) or 0

    # 按 video_type 分组
    rows = db.execute(
        select(MusicVideo.video_type, func.count(MusicVideo.id))
        .where(active)
        .group_by(MusicVideo.video_type)
    ).all()
    breakdown = {vt or "Other": cnt for vt, cnt in rows}

    total_artists = db.scalar(
        select(func.count(Artist.id)).where(Artist.deleted_at.is_(None))
    ) or 0
    total_groups = db.scalar(
        select(func.count(Group.id)).where(Group.deleted_at.is_(None))
    ) or 0
    total_songs = db.scalar(
        select(func.count(Song.id)).where(Song.deleted_at.is_(None))
    ) or 0
    total_albums = db.scalar(
        select(func.count(Album.id)).where(Album.deleted_at.is_(None))
    ) or 0

    return DashboardStats(
        total_videos=total_videos,
        incoming_count=incoming,
        library_count=library,
        total_artists=total_artists,
        total_groups=total_groups,
        total_songs=total_songs,
        total_albums=total_albums,
        video_type_breakdown=breakdown,
    )
