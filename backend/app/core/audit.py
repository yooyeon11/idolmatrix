"""操作审计：把「谁、何时、改了哪个实体的什么字段」落到 `audit_logs`。

为什么放在中间件而不是装饰器：写接口分布在十几个路由里，逐个包装饰器
容易漏；中间件只要看到写方法就能兜住，**即使某个路由没埋 diff，
也至少留下一行「谁调了这个接口」**。

字段级 diff 靠路由主动埋点（`mark_before` / `mark_changes`）：

    before = mark_before(artist, data.keys())
    ... 修改并 commit ...
    mark_changes(request, before, data)

拿不到 diff 的路径不会报错，只是 `changes` 为空。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime
from time import perf_counter
from typing import Any, Iterable, Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.database import SessionLocal
from app.core.security import request_client_ip
from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# 高频/无回溯价值的写路径：播放会话、观看心跳、登录登出
_SKIP_PREFIXES = (
    "/api/playback",
    "/api/stats/watch",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/status",
)

# 路径前缀 → 实体名（审计查询按这个筛）
_ENTITY_MAP: dict[str, str] = {
    "/api/artists": "artist",
    "/api/groups": "group",
    "/api/songs": "song",
    "/api/albums": "album",
    "/api/companies": "company",
    "/api/company-relations": "company_relation",
    "/api/memberships": "membership",
    "/api/music-videos": "music_video",
    "/api/video-collections": "video_collection",
    "/api/video-publish": "video_publish",
    "/api/ingest": "ingest",
    "/api/library": "library",
    "/api/uploads": "upload",
    "/api/photos": "photo",
    "/api/downloads": "download",
    "/api/api-tokens": "api_token",
}

_MAX_CHANGE_CHARS = 500


def _text(value: Any) -> str:
    """把任意字段值变成可比对、可存储的短文本。"""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, dict, tuple)):
        try:
            raw = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            raw = str(value)
    else:
        raw = str(value)
    return raw[:_MAX_CHANGE_CHARS] + ("…" if len(raw) > _MAX_CHANGE_CHARS else "")


def mark_before(entity: Any, fields: Iterable[str]) -> dict[str, str]:
    """修改前抓一份旧值（只抓本次要改的字段）。"""
    return {f: _text(getattr(entity, f, None)) for f in fields}


def mark_changes(
    request: Optional[Request], before: dict[str, str], data: dict[str, Any]
) -> dict[str, dict[str, str]]:
    """比对并写入 request.state，供中间件落库。返回 diff 方便测试。"""
    changes: dict[str, dict[str, str]] = {}
    for key, new_value in data.items():
        old = before.get(key, "")
        new = _text(new_value)
        if old != new:
            changes[key] = {"from": old, "to": new}
    if request is not None and changes:
        request.state.audit_changes = changes
    return changes


def _entity_of(path: str) -> tuple[Optional[str], Optional[int]]:
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2 or parts[0] != "api":
        return None, None
    prefix = "/" + "/".join(parts[:2])
    entity = _ENTITY_MAP.get(prefix)
    entity_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    return entity, entity_id


def _action_of(method: str) -> str:
    return {"POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete"}.get(
        method, "other"
    )


class AuditMiddleware(BaseHTTPMiddleware):
    """写操作落一行审计。任何异常都不影响业务请求。"""

    async def dispatch(self, request: Request, call_next):
        method = request.method.upper()
        path = request.url.path
        if (
            method not in WRITE_METHODS
            or not path.startswith("/api/")
            or any(path.startswith(p) for p in _SKIP_PREFIXES)
        ):
            return await call_next(request)

        started = perf_counter()
        response = await call_next(request)

        # 只记「带着卡被拒」的失败：匿名扫描器每天刷几百次 401，
        # 全记下来会把日志表冲爆，而且没有回溯价值。
        status = int(getattr(response, "status_code", 0) or 0)
        if status >= 400 and not (request.headers.get("x-api-token") or "").strip():
            return response

        try:
            actor = getattr(request.state, "audit_actor", None)
            if not isinstance(actor, dict):
                user = getattr(request.state, "current_user", None)
                label = getattr(user, "username", "") if user is not None else ""
                actor = {"type": "web", "label": label or "anonymous"}

            changes = getattr(request.state, "audit_changes", None)
            changes_json = None
            if isinstance(changes, dict) and changes:
                raw = json.dumps(changes, ensure_ascii=False)
                changes_json = raw[:8000]

            entity_type, entity_id = _entity_of(path)
            row = AuditLog(
                request_id=(request.headers.get("x-request-id") or uuid.uuid4().hex)[:36],
                actor_type=str(actor.get("type") or "web")[:12],
                actor_label=str(actor.get("label") or "")[:80],
                method=method,
                path=path[:300],
                entity_type=entity_type,
                entity_id=entity_id,
                action=_action_of(method),
                status_code=int(getattr(response, "status_code", 0) or 0),
                changes_json=changes_json,
                ip=request_client_ip(request)[:64] or None,
                duration_ms=int((perf_counter() - started) * 1000),
            )
            db = SessionLocal()
            try:
                db.add(row)
                db.commit()
            finally:
                db.close()
        except Exception as e:  # noqa: BLE001 - 审计失败绝不能影响业务
            logger.warning("审计日志写入失败：%s", e)

        return response


def prune(db, keep_days: int = 180) -> int:
    """删掉过期审计。周期清理任务调用。"""
    from datetime import timedelta

    from sqlalchemy import delete

    cutoff = datetime.now() - timedelta(days=max(1, keep_days))
    result = db.execute(delete(AuditLog).where(AuditLog.created_at < cutoff))
    db.commit()
    return int(result.rowcount or 0)
