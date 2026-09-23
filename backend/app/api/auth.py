"""登录 / 初始化 / 当前用户。"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.deps import CurrentUserDep, DbDep
from sqlalchemy import select

from app.core.security import (
    COOKIE_NAME,
    clear_session_cookie,
    csrf_origin_allowed,
    hash_token,
    is_private_client_ip,
    request_client_ip,
    set_session_cookie,
)
from app.models.auth_session import AuthSession
from app.models.user import User
from app.services.auth_service import (
    AuthError,
    auth_is_off,
    bootstrap_owner,
    change_password,
    change_username,
    count_users,
    create_agent,
    delete_agent,
    list_accounts,
    login,
    load_session,
    revoke_all_sessions,
    revoke_session,
)

logger = logging.getLogger("app.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = ""
    password: str = ""


class BootstrapBody(BaseModel):
    username: str = ""
    password: str = ""
    display_name: str = ""
    token: str = ""


class MePatch(BaseModel):
    display_name: Optional[str] = Field(None, max_length=80)
    username: Optional[str] = Field(None, max_length=32)


class CreateUserBody(BaseModel):
    username: str = ""
    password: str = ""
    display_name: str = ""


class PasswordBody(BaseModel):
    old_password: str = ""
    new_password: str = ""


def _raise(err: AuthError) -> None:
    headers = {}
    if err.retry_after:
        headers["Retry-After"] = str(err.retry_after)
    raise HTTPException(err.status, err.detail, headers=headers or None)


def _client_ip(request: Request) -> str:
    return request_client_ip(request)


def _user_json(user) -> dict:
    return {
        "id": user.id,
        "uid": getattr(user, "uid", ""),
        "username": user.username,
        "display_name": getattr(user, "display_name", None) or user.username,
        "role": getattr(user, "role", "owner"),
    }


@router.get("/status")
def auth_status(request: Request, db: DbDep):
    n = count_users(db)
    token = request.cookies.get(COOKIE_NAME) or ""
    current = load_session(db, token, touch=False) if token else None
    auth_required = not auth_is_off()
    token_configured = bool((settings.bootstrap_token or "").strip())
    # 公网且未配口令时也提示要填口令（实际会因服务器未配置而失败，避免空窗抢注）
    need_token = n == 0 and auth_required and (
        token_configured or not is_private_client_ip(_client_ip(request))
    )
    return {
        "authenticated": current is not None,
        "bootstrap_required": n == 0 and auth_required,
        "auth_required": auth_required,
        "bootstrap_token_required": need_token,
        "user": _user_json(current) if current else None,
    }


@router.post("/login")
def auth_login(payload: LoginBody, request: Request, response: Response, db: DbDep):
    if request.method == "POST" and not csrf_origin_allowed(request):
        raise HTTPException(403, "跨站请求被拒绝")
    try:
        user, token = login(
            db,
            payload.username,
            payload.password,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent") or "",
        )
    except AuthError as e:
        _raise(e)
    set_session_cookie(response, request, token)
    return {"user": _user_json(user)}


@router.post("/bootstrap")
def auth_bootstrap(payload: BootstrapBody, request: Request, response: Response):
    if not csrf_origin_allowed(request):
        raise HTTPException(403, "跨站请求被拒绝")
    try:
        _uid, token = bootstrap_owner(
            payload.username,
            payload.password,
            payload.display_name,
            token=payload.token,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent") or "",
        )
    except AuthError as e:
        _raise(e)
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        user = db.get(User, _uid)
        set_session_cookie(response, request, token)
        return {"user": _user_json(user) if user else {"id": _uid, "username": payload.username}}
    finally:
        db.close()


@router.post("/logout", status_code=204)
def auth_logout(request: Request, response: Response, db: DbDep):
    token = request.cookies.get(COOKIE_NAME) or ""
    if token:
        th = hash_token(token)
        row = db.scalar(select(AuthSession).where(AuthSession.token_hash == th))
        if row is not None:
            revoke_session(db, row)
            db.commit()
    clear_session_cookie(response, request)
    return Response(status_code=204)


@router.post("/logout-all", status_code=204)
def auth_logout_all(request: Request, response: Response, db: DbDep, user: CurrentUserDep):
    revoke_all_sessions(db, user.id)
    db.commit()
    clear_session_cookie(response, request)
    return Response(status_code=204)


@router.get("/me")
def auth_me(user: CurrentUserDep):
    return {"user": _user_json(user)}


@router.patch("/me")
def auth_patch_me(payload: MePatch, db: DbDep, user: CurrentUserDep):
    row = db.get(User, user.id)
    if row is None:
        raise HTTPException(404, "用户不存在")
    if payload.username is not None:
        try:
            row = change_username(db, row, payload.username)
        except AuthError as e:
            _raise(e)
    if payload.display_name is not None:
        text = payload.display_name.strip()
        row.display_name = text or row.username
        db.commit()
        db.refresh(row)
    return {"user": _user_json(row)}


@router.get("/users")
def auth_list_users(db: DbDep, user: CurrentUserDep):
    try:
        rows = list_accounts(db, user)
    except AuthError as e:
        _raise(e)
    return {"items": [_user_json(r) for r in rows]}


@router.post("/users")
def auth_create_user(payload: CreateUserBody, db: DbDep, user: CurrentUserDep):
    try:
        row = create_agent(db, user, payload.username, payload.password, payload.display_name)
    except AuthError as e:
        _raise(e)
    return {"user": _user_json(row)}


@router.delete("/users/{user_id}", status_code=204)
def auth_delete_user(user_id: int, db: DbDep, user: CurrentUserDep):
    try:
        delete_agent(db, user, user_id)
    except AuthError as e:
        _raise(e)
    return Response(status_code=204)


@router.post("/me/password")
def auth_change_password(
    payload: PasswordBody, request: Request, response: Response, db: DbDep, user: CurrentUserDep
):
    row = db.get(User, user.id)
    if row is None:
        raise HTTPException(404, "用户不存在")
    try:
        token = change_password(
            db,
            row,
            payload.old_password,
            payload.new_password,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent") or "",
        )
    except AuthError as e:
        _raise(e)
    set_session_cookie(response, request, token)
    return {"user": _user_json(row)}
