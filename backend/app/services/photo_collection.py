"""照片收藏夹：按单张照片 uid 收藏，一张图可进多个夹。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models.photo import Photo, PhotoCollection, PhotoCollectionItem
from app.services.photo_service import (
    PhotoServiceError,
    aggregate_authors,
    build_feed_items,
    photo_order_clauses,
    search_filter_clauses,
)


def _require_name(name: str) -> str:
    text = (name or "").strip()
    if not text:
        raise PhotoServiceError("收藏夹名称不能为空")
    return text


def resolve_photo(
    db: Session, *, photo_id: Optional[int] = None, photo_uid: Optional[str] = None
) -> Photo:
    photo = None
    if photo_uid and photo_uid.strip():
        photo = Photo.get_by_uid(db, photo_uid.strip())
    elif photo_id:
        photo = Photo.get_active(db, photo_id)
    if not photo:
        raise PhotoServiceError("照片不存在")
    return photo


def collection_stats(db: Session, collection_id: int) -> tuple[int, Optional[int]]:
    count = db.scalar(
        select(func.count())
        .select_from(PhotoCollectionItem)
        .join(Photo, Photo.id == PhotoCollectionItem.photo_id)
        .where(
            PhotoCollectionItem.collection_id == collection_id,
            Photo.active_filter(),
        )
    ) or 0
    cover_id = db.scalar(
        select(Photo.id)
        .join(PhotoCollectionItem, PhotoCollectionItem.photo_id == Photo.id)
        .where(
            PhotoCollectionItem.collection_id == collection_id,
            Photo.active_filter(),
        )
        .order_by(PhotoCollectionItem.created_at.desc(), Photo.id.desc())
        .limit(1)
    )
    return int(count), cover_id


def collection_to_read(db: Session, col: PhotoCollection) -> dict:
    count, cover_id = collection_stats(db, col.id)
    return {
        "id": col.id,
        "uid": col.uid,
        "name": col.name,
        "description": col.description,
        "photo_count": count,
        "cover_photo_id": cover_id,
        "created_at": col.created_at,
        "updated_at": col.updated_at,
        "deleted_at": col.deleted_at,
    }


def list_collections(db: Session) -> list[PhotoCollection]:
    return list(
        db.scalars(
            select(PhotoCollection)
            .where(PhotoCollection.active_filter())
            .order_by(PhotoCollection.updated_at.desc(), PhotoCollection.id.desc())
        ).all()
    )


def get_collection(db: Session, collection_id: int) -> PhotoCollection:
    col = PhotoCollection.get_active(db, collection_id)
    if not col:
        raise PhotoServiceError("收藏夹不存在")
    return col


def get_collection_by_uid(db: Session, uid: str) -> PhotoCollection:
    col = PhotoCollection.get_by_uid(db, uid)
    if not col:
        raise PhotoServiceError("收藏夹不存在")
    return col


def create_collection(
    db: Session, *, name: str, description: Optional[str] = None
) -> PhotoCollection:
    col = PhotoCollection(name=_require_name(name), description=(description or None))
    db.add(col)
    db.commit()
    db.refresh(col)
    return col


def update_collection(
    db: Session,
    col: PhotoCollection,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> PhotoCollection:
    if name is not None:
        col.name = _require_name(name)
    if description is not None:
        col.description = description.strip() or None
    db.commit()
    db.refresh(col)
    return col


def delete_collection(db: Session, col: PhotoCollection) -> None:
    col.soft_delete()
    db.commit()


def add_photo(db: Session, col: PhotoCollection, photo: Photo) -> PhotoCollectionItem:
    existing = db.scalar(
        select(PhotoCollectionItem).where(
            PhotoCollectionItem.collection_id == col.id,
            PhotoCollectionItem.photo_id == photo.id,
        )
    )
    if existing:
        return existing
    item = PhotoCollectionItem(
        collection_id=col.id,
        photo_id=photo.id,
        photo_uid=photo.uid,
    )
    db.add(item)
    col.updated_at = datetime.utcnow()
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        existing = db.scalar(
            select(PhotoCollectionItem).where(
                PhotoCollectionItem.collection_id == col.id,
                PhotoCollectionItem.photo_id == photo.id,
            )
        )
        if existing:
            return existing
        raise PhotoServiceError("加入收藏夹失败") from e
    db.refresh(item)
    db.refresh(col)
    return item


def remove_photo(db: Session, col: PhotoCollection, photo: Photo) -> bool:
    item = db.scalar(
        select(PhotoCollectionItem).where(
            PhotoCollectionItem.collection_id == col.id,
            PhotoCollectionItem.photo_id == photo.id,
        )
    )
    if not item:
        return False
    db.delete(item)
    col.updated_at = datetime.utcnow()
    db.commit()
    return True


def collection_photos(
    db: Session,
    col: PhotoCollection,
    *,
    page: int,
    page_size: int,
    scene: Optional[str] = None,
    shot: Optional[str] = None,
    shoes_type: Optional[str] = None,
    hosiery_present: Optional[str] = None,
    analyzed: Optional[bool] = None,
    tags: Optional[list[str]] = None,
    q: Optional[str] = None,
    author: Optional[str] = None,
    sort_by: str = "date",
    sort_dir: str = "desc",
) -> dict:
    from app.services.photo_analysis import analysis_filter_clauses

    stmt = (
        select(Photo)
        .options(selectinload(Photo.source))
        .join(PhotoCollectionItem, PhotoCollectionItem.photo_id == Photo.id)
        .where(
            PhotoCollectionItem.collection_id == col.id,
            Photo.active_filter(),
        )
        .order_by(*photo_order_clauses(sort_by, sort_dir))
    )
    # 收藏夹按单张展示，搜索不做帖子级扩展
    for clause in search_filter_clauses(
        (q or "").strip() or None,
        (author or "").strip() or None,
        expand_posts=False,
    ):
        stmt = stmt.where(clause)
    for clause in analysis_filter_clauses(
        scene=scene,
        shot=shot,
        shoes_type=shoes_type,
        hosiery_present=hosiery_present,
        analyzed=analyzed,
        tags=tags,
    ):
        stmt = stmt.where(clause)
    photos = list(db.scalars(stmt).all())
    items = build_feed_items(photos, "wall", sort_by, sort_dir)
    total = len(items)
    offset = max(page - 1, 0) * page_size
    return {
        "items": items[offset : offset + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "source_count": 1,
        "photo_count": total,
        "last_scanned_at": None,
    }


def collection_filter_options(db: Session, col: PhotoCollection) -> dict:
    """收藏夹内全部照片的筛选选项：自由标签 + 发布者（不受筛选影响）。"""
    from app.services.photo_analysis import aggregate_tags

    photos = db.scalars(
        select(Photo)
        .join(PhotoCollectionItem, PhotoCollectionItem.photo_id == Photo.id)
        .where(
            PhotoCollectionItem.collection_id == col.id,
            Photo.active_filter(),
        )
    ).all()
    return {
        "options": aggregate_tags(list(photos)),
        "authors": aggregate_authors(list(photos)),
    }


def memberships_for_photos(db: Session, photo_ids: list[int]) -> dict[int, list[int]]:
    result: dict[int, list[int]] = {int(i): [] for i in photo_ids}
    if not photo_ids:
        return result
    rows = db.execute(
        select(PhotoCollectionItem.photo_id, PhotoCollectionItem.collection_id)
        .join(PhotoCollection, PhotoCollection.id == PhotoCollectionItem.collection_id)
        .where(
            PhotoCollectionItem.photo_id.in_(list(result.keys())),
            PhotoCollection.active_filter(),
        )
        .order_by(PhotoCollectionItem.collection_id.asc())
    ).all()
    for photo_id, collection_id in rows:
        result.setdefault(int(photo_id), []).append(int(collection_id))
    return result
