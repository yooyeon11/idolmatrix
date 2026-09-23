"""通用依赖：分页参数、Session 注入、登录。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import COOKIE_NAME, hash_token
from app.services.auth_service import (
    AuthError,
    CurrentUser,
    auth_is_off,
    cached_user_id,
    cache_user_id,
    load_session,
    require_owner as _require_owner,
)

PUBLIC_EXACT = {
    ("GET", "/api/system/health"),
    ("GET", "/api/auth/status"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/bootstrap"),
    ("POST", "/api/auth/logout"),
}

_TOUCH_SKIP = (
    re.compile(r"^/api/playback/sessions/\d+/manifest\.m3u8$"),
    re.compile(r"^/api/playback/sessions/\d+/segments/[^/]+$"),
    re.compile(r"^/api/playback/sessions/\d+/stream$"),
    re.compile(r"^/api/music-videos/\d+/(thumbnail|cover)(/.*)?$"),
    re.compile(r"^/api/music-videos/\d+/cover-frames(/.*)?$"),
    re.compile(r"^/api/photos/\d+/(file|thumb)$"),
    re.compile(r"^/api/(artists|groups)/\d+/(avatar|banner)$"),
    re.compile(r"^/api/albums/\d+/cover$"),
)


def is_public(method: str, path: str) -> bool:
    return (method.upper(), path) in PUBLIC_EXACT


def skip_session_touch(method: str, path: str) -> bool:
    if method.upper() != "GET":
        return False
    return any(p.match(path) for p in _TOUCH_SKIP)


@dataclass
class PaginationParams:
    page: int = 1
    page_size: int = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


def get_pagination(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> PaginationParams:
    return PaginationParams(page=page, page_size=page_size)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    cached = getattr(request.state, "current_user", None)
    if isinstance(cached, CurrentUser):
        return cached
    if auth_is_off():
        raise HTTPException(401, "未登录")
    token = request.cookies.get(COOKIE_NAME) or ""
    touch = not skip_session_touch(request.method, request.url.path)
    if skip_session_touch(request.method, request.url.path) and token:
        uid = cached_user_id(hash_token(token))
        if uid is not None:
            user = load_session(db, token, touch=False)
            if user is not None:
                request.state.current_user = user
                return user
    user = load_session(db, token, touch=touch)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录")
    if skip_session_touch(request.method, request.url.path):
        cache_user_id(hash_token(token), user.id)
    request.state.current_user = user
    return user


def require_login(
    request: Request, db: Session = Depends(get_db)
) -> Optional[CurrentUser]:
    if is_public(request.method, request.url.path) or auth_is_off():
        return getattr(request.state, "current_user", None)
    return get_current_user(request, db)


def require_owner_user(
    request: Request, db: Session = Depends(get_db)
) -> Optional[CurrentUser]:
    """写敏感配置仅主账号。AUTH_REQUIRED=false 时不拦截（救火阀）。"""
    if auth_is_off():
        return None
    user = get_current_user(request, db)
    try:
        _require_owner(user)
    except AuthError as e:
        raise HTTPException(e.status, e.detail) from e
    return user


DbDep = Annotated[Session, Depends(get_db)]
PaginationDep = Annotated[PaginationParams, Depends(get_pagination)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
LoginDep = Annotated[Optional[CurrentUser], Depends(require_login)]
OwnerDep = Annotated[Optional[CurrentUser], Depends(require_owner_user)]
