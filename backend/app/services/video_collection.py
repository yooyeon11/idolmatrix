"""视频收藏夹：按单个视频 uid 收藏，一个视频可进多个夹。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.album import AlbumTrack
from app.models.music_video import MusicVideo, MusicVideoTrack
from app.models.song import Song
from app.models.video_collection import VideoCollection, VideoCollectionItem
from app.services.photo_service import PhotoServiceError


def _video_options():
    return (
        joinedload(MusicVideo.song),
        joinedload(MusicVideo.subject_artist),
        selectinload(MusicVideo.songs).selectinload(Song.album_tracks).joinedload(AlbumTrack.album),
        selectinload(MusicVideo.albums),
        selectinload(MusicVideo.artists),
        selectinload(MusicVideo.groups),
        selectinload(MusicVideo.video_tracks).selectinload(MusicVideoTrack.albums),
        selectinload(MusicVideo.video_tracks).joinedload(MusicVideoTrack.song),
    )


def _require_name(name: str) -> str:
    text = (name or "").strip()
    if not text:
        raise PhotoServiceError("收藏夹名称不能为空")
    return text


def resolve_video(
    db: Session, *, video_id: Optional[int] = None, video_uid: Optional[str] = None
) -> MusicVideo:
    video = None
    if video_uid and video_uid.strip():
        video = MusicVideo.get_by_uid(db, video_uid.strip())
    elif video_id:
        video = MusicVideo.get_active(db, video_id)
    if not video:
        raise PhotoServiceError("视频不存在")
    return video


def collection_stats(db: Session, collection_id: int) -> tuple[int, Optional[int]]:
    count = db.scalar(
        select(func.count())
        .select_from(VideoCollectionItem)
        .join(MusicVideo, MusicVideo.id == VideoCollectionItem.music_video_id)
        .where(
            VideoCollectionItem.collection_id == collection_id,
            MusicVideo.active_filter(),
        )
    ) or 0
    cover_id = db.scalar(
        select(MusicVideo.id)
        .join(VideoCollectionItem, VideoCollectionItem.music_video_id == MusicVideo.id)
        .where(
            VideoCollectionItem.collection_id == collection_id,
            MusicVideo.active_filter(),
        )
        .order_by(VideoCollectionItem.created_at.desc(), MusicVideo.id.desc())
        .limit(1)
    )
    return int(count), cover_id


def collection_to_read(db: Session, col: VideoCollection) -> dict:
    count, cover_id = collection_stats(db, col.id)
    return {
        "id": col.id,
        "uid": col.uid,
        "name": col.name,
        "description": col.description,
        "video_count": count,
        "cover_video_id": cover_id,
        "created_at": col.created_at,
        "updated_at": col.updated_at,
        "deleted_at": col.deleted_at,
    }


def list_collections(db: Session) -> list[VideoCollection]:
    return list(
        db.scalars(
            select(VideoCollection)
            .where(VideoCollection.active_filter())
            .order_by(VideoCollection.updated_at.desc(), VideoCollection.id.desc())
        ).all()
    )


def get_collection(db: Session, collection_id: int) -> VideoCollection:
    col = VideoCollection.get_active(db, collection_id)
    if not col:
        raise PhotoServiceError("收藏夹不存在")
    return col


def get_collection_by_uid(db: Session, uid: str) -> VideoCollection:
    col = VideoCollection.get_by_uid(db, uid)
    if not col:
        raise PhotoServiceError("收藏夹不存在")
    return col


def create_collection(
    db: Session, *, name: str, description: Optional[str] = None
) -> VideoCollection:
    col = VideoCollection(name=_require_name(name), description=(description or None))
    db.add(col)
    db.commit()
    db.refresh(col)
    return col


def update_collection(
    db: Session,
    col: VideoCollection,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> VideoCollection:
    if name is not None:
        col.name = _require_name(name)
    if description is not None:
        col.description = description.strip() or None
    db.commit()
    db.refresh(col)
    return col


def delete_collection(db: Session, col: VideoCollection) -> None:
    col.soft_delete()
    db.commit()


def add_video(db: Session, col: VideoCollection, video: MusicVideo) -> VideoCollectionItem:
    existing = db.scalar(
        select(VideoCollectionItem).where(
            VideoCollectionItem.collection_id == col.id,
            VideoCollectionItem.music_video_id == video.id,
        )
    )
    if existing:
        return existing
    item = VideoCollectionItem(
        collection_id=col.id,
        music_video_id=video.id,
        video_uid=video.uid,
    )
    db.add(item)
    col.updated_at = datetime.utcnow()
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        existing = db.scalar(
            select(VideoCollectionItem).where(
                VideoCollectionItem.collection_id == col.id,
                VideoCollectionItem.music_video_id == video.id,
            )
        )
        if existing:
            return existing
        raise PhotoServiceError("加入收藏夹失败") from e
    db.refresh(item)
    db.refresh(col)
    return item


def remove_video(db: Session, col: VideoCollection, video: MusicVideo) -> bool:
    item = db.scalar(
        select(VideoCollectionItem).where(
            VideoCollectionItem.collection_id == col.id,
            VideoCollectionItem.music_video_id == video.id,
        )
    )
    if not item:
        return False
    db.delete(item)
    col.updated_at = datetime.utcnow()
    db.commit()
    return True


def collection_videos(
    db: Session, col: VideoCollection, *, page: int, page_size: int
) -> tuple[list[MusicVideo], int]:
    total = (
        db.scalar(
            select(func.count())
            .select_from(VideoCollectionItem)
            .join(MusicVideo, MusicVideo.id == VideoCollectionItem.music_video_id)
            .where(
                VideoCollectionItem.collection_id == col.id,
                MusicVideo.active_filter(),
            )
        )
        or 0
    )
    offset = max(page - 1, 0) * page_size
    videos = list(
        db.scalars(
            select(MusicVideo)
            .options(*_video_options())
            .join(VideoCollectionItem, VideoCollectionItem.music_video_id == MusicVideo.id)
            .where(
                VideoCollectionItem.collection_id == col.id,
                MusicVideo.active_filter(),
            )
            .order_by(VideoCollectionItem.created_at.desc(), MusicVideo.id.desc())
            .offset(offset)
            .limit(page_size)
        ).unique().all()
    )
    return videos, int(total)


def memberships_for_videos(db: Session, video_ids: list[int]) -> dict[int, list[int]]:
    result: dict[int, list[int]] = {int(i): [] for i in video_ids}
    if not video_ids:
        return result
    rows = db.execute(
        select(VideoCollectionItem.music_video_id, VideoCollectionItem.collection_id)
        .join(VideoCollection, VideoCollection.id == VideoCollectionItem.collection_id)
        .where(
            VideoCollectionItem.music_video_id.in_(list(result.keys())),
            VideoCollection.active_filter(),
        )
        .order_by(VideoCollectionItem.collection_id.asc())
    ).all()
    for video_id, collection_id in rows:
        result.setdefault(int(video_id), []).append(int(collection_id))
    return result
