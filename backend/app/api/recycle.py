"""回收站：列出软删除实体并恢复 / 永久删除。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, PaginationDep
from app.models.album import Album
from app.models.artist import Artist
from app.models.company import Company
from app.models.group import Group
from app.models.music_video import MusicVideo
from app.models.song import Song
from app.schemas import PageResponse, RecycleItem
from app.services.library_service import _purge_derived_for, resolve_video_abs_path

router = APIRouter(prefix="/recycle", tags=["recycle"])

RecycleKind = Literal["videos", "artists", "groups", "songs", "albums", "companies"]

_MODELS = {
    "videos": MusicVideo,
    "artists": Artist,
    "groups": Group,
    "songs": Song,
    "albums": Album,
    "companies": Company,
}


def _to_item(kind: str, obj) -> RecycleItem:
    extra = None
    file_missing = False
    if kind == "videos":
        extra = getattr(obj, "video_type", None) or getattr(obj, "file_path", None)
        path = resolve_video_abs_path(obj)
        file_missing = path is None or not path.exists()
    elif kind == "groups":
        extra = getattr(obj, "group_type", None)
    elif kind == "songs":
        extra = getattr(obj, "song_type", None)
    elif kind == "albums":
        extra = getattr(obj, "album_type", None)
    return RecycleItem(
        id=obj.id,
        uid=getattr(obj, "uid", None),
        name=getattr(obj, "name", None) or f"#{obj.id}",
        chinese_name=getattr(obj, "chinese_name", None),
        deleted_at=obj.deleted_at,
        extra=extra,
        file_missing=file_missing,
    )


@router.get("/{kind}", response_model=PageResponse[RecycleItem])
def list_deleted(kind: RecycleKind, db: DbDep, pagination: PaginationDep, q: str | None = None):
    model = _MODELS[kind]
    stmt = select(model).where(model.deleted_at.is_not(None))
    if q and q.strip():
        like = f"%{q.strip()}%"
        clauses = [model.name.ilike(like)]
        if hasattr(model, "chinese_name"):
            clauses.append(model.chinese_name.ilike(like))
        stmt = stmt.where(or_(*clauses))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(model.deleted_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    ).all()
    return PageResponse(
        items=[_to_item(kind, it) for it in items],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post("/{kind}/{item_id}/restore", response_model=RecycleItem)
def restore_deleted(kind: RecycleKind, item_id: int, db: DbDep):
    """恢复软删除实体（仅清除 deleted_at）。

    注意：软删除艺人/组合/公司时已静默拆除成员关系、公司关系、
    MV 主体指针、小分队上级等边；恢复 **不会** 自动重建这些关系，
    需在详情页重新挂接或重新生长。
    """
    model = _MODELS[kind]
    obj = db.get(model, item_id)
    if obj is None or obj.deleted_at is None:
        raise HTTPException(404, "记录不存在或未删除")
    obj.restore()
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            409,
            "无法恢复：已有活跃记录占用相同唯一标识（如文件哈希或来源 ID）",
        ) from e
    db.refresh(obj)
    return _to_item(kind, obj)


@router.delete("/{kind}/{item_id}", response_model=RecycleItem)
def purge_deleted(kind: RecycleKind, item_id: int, db: DbDep):
    """永久删除一条软删除记录（硬删数据库行，不可恢复）。

    - 视频会同步清理派生文件：缩略图、列表位封面变体、转码缓存；
      sidecar（info.json / 封面）仅在视频文件确认不存在时一并删除。
      不删除 library/incoming 里的视频文件本身（与失效清理惯例一致）。
    - 其他实体软删除时关系边已拆除，直接删行。
    """
    model = _MODELS[kind]
    obj = db.get(model, item_id)
    if obj is None or obj.deleted_at is None:
        raise HTTPException(404, "记录不存在或未删除")
    item = _to_item(kind, obj)
    if kind == "videos":
        _purge_derived_for(db, obj)
    db.delete(obj)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "无法删除：仍有活跃记录引用该实体") from e
    return item
