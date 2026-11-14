"""API 令牌的签发、校验与权限判定。

调用链：`deps.require_login` → `deps.get_external_principal` → 本模块。

权限模型（刻意保守）：

    范围        隐含权限              能做什么
    read        read                  读艺人/组合/视频/入库清单
    edit        read + edit           改元数据、关联、上传头像
    ingest      read + ingest         扫描待整理、写清单、accept/reject
    admin       全部                  edit + ingest + 删除/回收站

不提供「按路径细分」的粒度：卡片越细，配置越容易配错，而 AI 拿不到
admin 就删不了东西 —— 这条边界已经足够。
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_token
from app.models.api_token import ApiToken

SCOPES: tuple[str, ...] = ("read", "edit", "ingest", "admin")

# admin ⊃ edit ⊃ read；ingest 独立于 edit，但同样包含 read
_IMPLIES: dict[str, set[str]] = {
    "read": {"read"},
    "edit": {"read", "edit"},
    "ingest": {"read", "ingest"},
    "admin": {"read", "edit", "ingest", "admin"},
}

TOKEN_PREFIX = "imk_"

# 低于这个间隔不重复写 last_used_at，避免每个请求都产生一次写事务
_TOUCH_INTERVAL = timedelta(seconds=60)


class TokenError(Exception):
    """令牌不可用（不存在 / 已撤销 / 已过期 / 权限不足）。"""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _normalize_scopes(values: Iterable[str]) -> list[str]:
    picked = {v for v in values if v in SCOPES}
    if not picked:
        picked = {"read"}
    # admin 已包含其余权限，冗余项去掉，列表更易读
    if "admin" in picked:
        return ["admin"]
    return [s for s in SCOPES if s in picked]


def split_scopes(row: ApiToken) -> list[str]:
    try:
        data = json.loads(row.scopes or "[]")
    except (ValueError, TypeError):
        return ["read"]
    return [str(x) for x in data if str(x) in SCOPES] or ["read"]


def has_scope(row: ApiToken, need: str) -> bool:
    granted: set[str] = set()
    for s in split_scopes(row):
        granted |= _IMPLIES.get(s, set())
    return need in granted


def required_scope(method: str, path: str) -> str:
    """根据请求方法与路径推出最低权限要求。"""
    m = (method or "GET").upper()
    if m in ("GET", "HEAD", "OPTIONS"):
        return "read"
    # 入库与待整理：扫描、清单、accept/reject 都属于 ingest
    if path.startswith("/api/ingest") or path.startswith("/api/library"):
        return "ingest"
    if m == "DELETE":
        return "admin"
    return "edit"


def issue(
    db: Session,
    *,
    name: str,
    scopes: Iterable[str] = ("read",),
    expires_days: Optional[int] = None,
    created_by: Optional[str] = None,
    note: Optional[str] = None,
) -> tuple[ApiToken, str]:
    """签发一张卡。返回 (记录, 明文) —— 明文只在这里出现一次。"""
    plain = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(
        name=name.strip(),
        token_hash=hash_token(plain),
        token_hint=plain[:12],
        scopes=json.dumps(_normalize_scopes(scopes), ensure_ascii=False),
        expires_at=(
            datetime.now() + timedelta(days=int(expires_days))
            if expires_days and int(expires_days) > 0
            else None
        ),
        created_by=created_by,
        note=(note or "").strip()[:200] or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, plain


def verify(
    db: Session,
    raw_token: str,
    *,
    ip: str = "",
    touch: bool = True,
) -> Optional[ApiToken]:
    """按哈希查卡并检查可用性。不存在返回 None，其余异常抛 TokenError。"""
    token = (raw_token or "").strip()
    if not token:
        return None
    row = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token)))
    if row is None:
        return None
    if row.revoked_at is not None:
        raise TokenError(401, "该 API 令牌已撤销")
    if row.expires_at is not None and row.expires_at < datetime.now():
        raise TokenError(401, "该 API 令牌已过期")
    if touch:
        now = datetime.now()
        if row.last_used_at is None or now - row.last_used_at >= _TOUCH_INTERVAL:
            row.last_used_at = now
            row.last_used_ip = (ip or "")[:64] or row.last_used_ip
            try:
                db.commit()
            except Exception:  # noqa: BLE001 - 使用时间写失败不该影响业务请求
                db.rollback()
    return row


def revoke(db: Session, token_id: int) -> bool:
    row = db.get(ApiToken, token_id)
    if row is None:
        return False
    if row.revoked_at is None:
        row.revoked_at = datetime.now()
        db.commit()
    return True


def list_tokens(db: Session) -> list[ApiToken]:
    return list(db.scalars(select(ApiToken).order_by(ApiToken.id.desc())).all())


def serialize(row: ApiToken) -> dict:
    """给前端的形态：绝不含 token_hash / 明文。"""
    now = datetime.now()
    expired = row.expires_at is not None and row.expires_at < now
    return {
        "id": row.id,
        "name": row.name,
        "hint": row.token_hint,
        "scopes": split_scopes(row),
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "last_used_ip": row.last_used_ip,
        "created_by": row.created_by,
        "note": row.note,
        "status": (
            "revoked" if row.revoked_at else "expired" if expired else "active"
        ),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
