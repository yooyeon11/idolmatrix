"""API 令牌管理（只给浏览器会话用，令牌自己不能发卡）。

为什么令牌不能调这里：`deps.is_token_route` 的白名单不含 `/api/api-tokens`，
外部请求即使带了合法 `X-API-Token` 也会落到会话鉴权 → 401。
这样「AI 拿到的卡」永远无法给自己提权或复制一张新卡。
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, OwnerDep
from app.services import api_token_service

router = APIRouter(prefix="/api-tokens", tags=["api-tokens"])


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    scopes: List[str] = Field(default_factory=lambda: ["read"])
    # 0 / None = 长期有效
    expires_days: Optional[int] = Field(default=None, ge=0, le=3650)
    note: Optional[str] = Field(default=None, max_length=200)


@router.get("")
def list_tokens(db: DbDep, _: OwnerDep):
    return [api_token_service.serialize(r) for r in api_token_service.list_tokens(db)]


@router.post("", status_code=201)
def create_token(payload: ApiTokenCreate, db: DbDep, user: OwnerDep):
    """签发一张卡：明文只在这一条响应里出现一次。"""
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "令牌名称不能为空")
    try:
        row, plain = api_token_service.issue(
            db,
            name=name,
            scopes=payload.scopes,
            expires_days=payload.expires_days,
            created_by=getattr(user, "username", None),
            note=payload.note,
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, f"令牌名称「{name}」已存在，换一个")
    return {"token": plain, **api_token_service.serialize(row)}


@router.post("/{token_id}/revoke")
def revoke_token(token_id: int, db: DbDep, _: OwnerDep):
    if not api_token_service.revoke(db, token_id):
        raise HTTPException(404, "令牌不存在")
    return {"ok": True}
