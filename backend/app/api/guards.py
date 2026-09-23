"""软删除关系完整性守卫：写路径统一校验核心实体 active / 名称。"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.artist import Artist
from app.models.group import Group


def require_active(db: Session, model, pk: int, label: str):
    """按内部 id 校验实体存在且未软删除；否则抛 404。

    用于创建/更新关系时的双方（或多方）active 校验。
    """
    obj = model.get_active(db, pk)
    if obj is None:
        raise HTTPException(404, f"{label} 不存在或已删除 (id={pk})")
    return obj


def ensure_name_ci_unique(
    db: Session,
    model,
    name: str,
    label: str,
    *,
    exclude_id: Optional[int] = None,
) -> None:
    """大小写不敏感重名校验：活跃实体中存在同名（不区分大小写）时抛 409。

    名字为空或仅空白时跳过；exclude_id 用于更新场景排除自身。
    """
    if not name or not name.strip():
        return
    stmt = select(model).where(
        func.lower(model.name) == name.strip().lower(),
        model.active_filter(),
    )
    if exclude_id is not None:
        stmt = stmt.where(model.id != exclude_id)
    existing = db.scalar(stmt.limit(1))
    if existing is not None:
        raise HTTPException(
            409, f"{label}名已存在（不区分大小写）: {existing.name}"
        )


def validate_release_artist(
    db: Session, release_artist_type, release_artist_id
) -> None:
    """多态发行主体校验：type=artist -> Artist，type=group -> Group。

    任一为 None（未指定）则跳过；type 非法抛 400。
    """
    if release_artist_type is None or release_artist_id is None:
        return
    if release_artist_type == "artist":
        require_active(db, Artist, release_artist_id, "Artist(发行主体)")
    elif release_artist_type == "group":
        require_active(db, Group, release_artist_id, "Group(发行主体)")
    else:
        raise HTTPException(
            400, f"不支持的 release_artist_type: {release_artist_type}"
        )
