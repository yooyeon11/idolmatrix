"""EntityFieldLock 路由：实体字段锁的读取与写入（跨设备持久化）。

锁只在前端约束「AI 分析」「站点获取」的写入，本路由仅负责存取；
locks 为稀疏 JSON，只保留 true 键，全解锁时删除行。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.guards import require_active
from app.core.deps import DbDep
from app.models.album import Album
from app.models.artist import Artist
from app.models.company import Company
from app.models.entity_field_lock import EntityFieldLock
from app.models.group import Group
from app.models.song import Song
from app.schemas.entity_lock import EntityLocksRead, EntityLocksUpdate

router = APIRouter(prefix="/entity-locks", tags=["entity-locks"])

# entity_type 与前端数据库 tab key 一致
LOCKABLE_TYPES = {
    "songs": Song,
    "albums": Album,
    "artists": Artist,
    "groups": Group,
    "companies": Company,
}

# 锁键长度上限：字段名或保留区块键（avatar / memberships）
_MAX_LOCK_KEY_LEN = 64


def _model_for(entity_type: str):
    model = LOCKABLE_TYPES.get(entity_type)
    if model is None:
        raise KeyError(entity_type)
    return model


def _clean_locks(raw: dict) -> dict[str, bool]:
    return {
        str(k).strip()[:_MAX_LOCK_KEY_LEN]: True
        for k, v in (raw or {}).items()
        if v
    }


@router.get("/{entity_type}/{entity_id}", response_model=EntityLocksRead)
def get_entity_locks(entity_type: str, entity_id: int, db: DbDep):
    try:
        model = _model_for(entity_type)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"未知实体类型：{entity_type}") from e
    require_active(db, model, entity_id, entity_type)
    row = db.scalar(
        select(EntityFieldLock).where(
            EntityFieldLock.entity_type == entity_type,
            EntityFieldLock.entity_id == entity_id,
        )
    )
    return EntityLocksRead(
        entity_type=entity_type,
        entity_id=entity_id,
        locks=(row.locks if row else {}) or {},
    )


@router.put("/{entity_type}/{entity_id}", response_model=EntityLocksRead)
def put_entity_locks(
    entity_type: str, entity_id: int, payload: EntityLocksUpdate, db: DbDep
):
    try:
        model = _model_for(entity_type)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"未知实体类型：{entity_type}") from e
    require_active(db, model, entity_id, entity_type)
    clean = _clean_locks(payload.locks)
    row = db.scalar(
        select(EntityFieldLock).where(
            EntityFieldLock.entity_type == entity_type,
            EntityFieldLock.entity_id == entity_id,
        )
    )
    if clean:
        if row is None:
            row = EntityFieldLock(
                entity_type=entity_type, entity_id=entity_id, locks=clean
            )
            db.add(row)
        else:
            row.locks = clean
    elif row is not None:
        db.delete(row)
    db.commit()
    return EntityLocksRead(
        entity_type=entity_type, entity_id=entity_id, locks=clean
    )
