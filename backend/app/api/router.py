"""API 路由聚合。所有子路由统一挂在 /api 下。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import require_login
from app.api import (
    ai,
    auth,
    app_settings,
    albums,
    artists,
    companies,
    company_relations,
    dashboard,
    data_health,
    db_views,
    entity_locks,
    groups,
    home,
    library,
    memberships,
    recycle,
    music_videos,
    playback,
    photos,
    providers,
    video_collections,
    songs,
    stats,
    system,
    uploaders,
    uploads,
)

api_router = APIRouter(prefix="/api", dependencies=[Depends(require_login)])
api_router.include_router(auth.router)
api_router.include_router(ai.router)
api_router.include_router(app_settings.router)
api_router.include_router(artists.router)
api_router.include_router(groups.router)
api_router.include_router(home.router)
api_router.include_router(memberships.router)
api_router.include_router(companies.router)
api_router.include_router(company_relations.router)
api_router.include_router(albums.router)
api_router.include_router(songs.router)
api_router.include_router(music_videos.router)
api_router.include_router(playback.router)
api_router.include_router(playback.diag_router)
api_router.include_router(library.router)
api_router.include_router(recycle.router)
api_router.include_router(dashboard.router)
api_router.include_router(stats.router)
api_router.include_router(providers.router)
api_router.include_router(system.router)
api_router.include_router(uploaders.router)
api_router.include_router(uploads.router)
api_router.include_router(photos.router)
api_router.include_router(video_collections.router)
api_router.include_router(entity_locks.router)
api_router.include_router(data_health.router)
api_router.include_router(db_views.router)
