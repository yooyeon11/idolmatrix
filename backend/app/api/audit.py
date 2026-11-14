"""操作审计查询：给人和 AI 自查「刚才改了什么」。

只读，且仅限浏览器会话（不进 `is_token_route` 白名单）—— 审计日志里
有实体改动的前后值，不该让外部令牌随意翻。
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.core.deps import DbDep, OwnerDep
from app.models.audit_log import AuditLog

router = APIRouter(prefix="/audit-logs", tags=["audit"])


def _row(r: AuditLog) -> dict:
    changes = None
    if r.changes_json:
        try:
            changes = json.loads(r.changes_json)
        except (ValueError, TypeError):
            changes = None
    return {
        "id": r.id,
        "request_id": r.request_id,
        "actor_type": r.actor_type,
        "actor_label": r.actor_label,
        "method": r.method,
        "path": r.path,
        "entity_type": r.entity_type,
        "entity_id": r.entity_id,
        "action": r.action,
        "status_code": r.status_code,
        "changes": changes,
        "ip": r.ip,
        "duration_ms": r.duration_ms,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("")
def list_audit_logs(
    db: DbDep,
    _: OwnerDep,
    entity_type: Optional[str] = Query(default=None),
    entity_id: Optional[int] = Query(default=None),
    actor_label: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(AuditLog)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if actor_label:
        stmt = stmt.where(AuditLog.actor_label == actor_label)
    stmt = stmt.order_by(AuditLog.id.desc()).limit(limit).offset(offset)
    rows = db.scalars(stmt).all()
    return {"items": [_row(r) for r in rows], "limit": limit, "offset": offset}
