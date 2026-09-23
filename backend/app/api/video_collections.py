"""视频收藏夹。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.music_videos import _to_read
from app.core.deps import DbDep
from app.schemas.common import PageResponse
from app.schemas.music_video import (
    MusicVideoRead,
    VideoCollectRequest,
    VideoCollectionCreate,
    VideoCollectionRead,
    VideoCollectionUpdate,
    VideoMembershipsResponse,
)
from app.services import video_collection
from app.services.photo_service import PhotoServiceError

router = APIRouter(prefix="/video-collections", tags=["video-collections"])


def _http(err: PhotoServiceError) -> HTTPException:
    return HTTPException(400, str(err))


def _read(db, col) -> VideoCollectionRead:
    return VideoCollectionRead(**video_collection.collection_to_read(db, col))


@router.get("", response_model=list[VideoCollectionRead])
def list_video_collections(db: DbDep):
    cols = video_collection.list_collections(db)
    return [_read(db, c) for c in cols]


@router.post("", response_model=VideoCollectionRead, status_code=201)
def create_video_collection(payload: VideoCollectionCreate, db: DbDep):
    try:
        col = video_collection.create_collection(
            db, name=payload.name, description=payload.description
        )
    except PhotoServiceError as e:
        raise _http(e) from e
    return _read(db, col)


@router.get("/memberships", response_model=VideoMembershipsResponse)
def video_collection_memberships(
    db: DbDep,
    video_ids: str = Query("", description="逗号分隔的视频 id"),
):
    ids: list[int] = []
    for part in (video_ids or "").split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return VideoMembershipsResponse(
        memberships=video_collection.memberships_for_videos(db, ids)
    )


@router.get("/by-uid/{uid}", response_model=VideoCollectionRead)
def get_video_collection_by_uid(uid: str, db: DbDep):
    try:
        col = video_collection.get_collection_by_uid(db, uid)
    except PhotoServiceError as e:
        raise HTTPException(404, str(e)) from e
    return _read(db, col)


@router.get("/{collection_id}", response_model=VideoCollectionRead)
def get_video_collection(collection_id: int, db: DbDep):
    try:
        col = video_collection.get_collection(db, collection_id)
    except PhotoServiceError as e:
        raise HTTPException(404, str(e)) from e
    return _read(db, col)


@router.patch("/{collection_id}", response_model=VideoCollectionRead)
def update_video_collection(collection_id: int, payload: VideoCollectionUpdate, db: DbDep):
    try:
        col = video_collection.get_collection(db, collection_id)
        col = video_collection.update_collection(
            db, col, name=payload.name, description=payload.description
        )
    except PhotoServiceError as e:
        raise _http(e) from e
    return _read(db, col)


@router.delete("/{collection_id}", status_code=204)
def delete_video_collection(collection_id: int, db: DbDep):
    try:
        col = video_collection.get_collection(db, collection_id)
    except PhotoServiceError as e:
        raise HTTPException(404, str(e)) from e
    video_collection.delete_collection(db, col)


@router.get("/{collection_id}/videos", response_model=PageResponse[MusicVideoRead])
def list_collection_videos(
    collection_id: int,
    db: DbDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
):
    try:
        col = video_collection.get_collection(db, collection_id)
        videos, total = video_collection.collection_videos(
            db, col, page=page, page_size=page_size
        )
    except PhotoServiceError as e:
        raise HTTPException(404, str(e)) from e
    return PageResponse(
        items=[_to_read(v) for v in videos],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{collection_id}/videos", response_model=VideoCollectionRead)
def add_video_to_collection(collection_id: int, payload: VideoCollectRequest, db: DbDep):
    try:
        col = video_collection.get_collection(db, collection_id)
        video = video_collection.resolve_video(
            db, video_id=payload.video_id, video_uid=payload.video_uid
        )
        video_collection.add_video(db, col, video)
        db.refresh(col)
    except PhotoServiceError as e:
        raise _http(e) from e
    return _read(db, col)


@router.delete("/{collection_id}/videos/{video_id}", response_model=VideoCollectionRead)
def remove_video_from_collection(collection_id: int, video_id: int, db: DbDep):
    try:
        col = video_collection.get_collection(db, collection_id)
        video = video_collection.resolve_video(db, video_id=video_id)
        video_collection.remove_video(db, col, video)
        db.refresh(col)
    except PhotoServiceError as e:
        raise _http(e) from e
    return _read(db, col)
