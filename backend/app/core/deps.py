"""通用依赖：分页参数、Session 注入、登录。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Optional

import hmac

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.core.security import COOKIE_NAME, hash_token, request_client_ip
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


# ===== 外部集成 Token（workbuddy 等）=====
# 覆盖「库内容管理」路由：待整理/入库、视频与资料库编辑、评分回写。
# 刻意不覆盖：设置(app-settings)、账号(auth)、系统(system)、上传器/外部服务等 ——
# Token 只管内容,不管配置与账号。
_TOKEN_API_RE = (
    re.compile(r"^/api/library(/.*)?$"),
    re.compile(r"^/api/music-videos(/.*)?$"),
    re.compile(r"^/api/(artists|groups|songs|albums)(/.*)?$"),
    re.compile(r"^/api/video-collections(/.*)?$"),
    # 入库清单：外置 Agent 用它读取待办、回写清单（只写清单，不写业务表）
    re.compile(r"^/api/ingest(/.*)?$"),
)


def is_token_route(path: str) -> bool:
    return any(p.match(path) for p in _TOKEN_API_RE)


def is_public(method: str, path: str) -> bool:
    # ⚠ Token 路由绝不能进这里：is_public = 完全免鉴权。Token 路由的放行
    # 只发生在「请求带了 X-API-Token 头」时（中间件跳过会话/CSRF），
    # 且凭据校验始终在 require_login 的 token 分支执行。
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


def get_external_principal(
    db: Session, token: str, request: Optional[Request] = None
) -> CurrentUser:
    """校验外部集成 Token，返回主账号身份。

    两条通道，新卡优先：

    1. `api_tokens` 表里的卡 —— 有权限范围、有效期、撤销状态与使用记录，
       审计归属记到「卡名」而不是主账号，能区分是哪个工具干的。
    2. 旧的 `app_settings.external_api.token` —— 兼容回退，等价 admin，
       审计记作 `legacy-token`。建议签发新卡后在设置页清空它。
    """
    from app.services import api_token_service
    from app.services import app_settings as app_settings_service

    provided = (token or "").strip()
    if not provided:
        raise HTTPException(401, "X-API-Token 缺失")

    actor_label = ""
    try:
        row = api_token_service.verify(
            db,
            provided,
            ip=request_client_ip(request) if request is not None else "",
        )
    except api_token_service.TokenError as e:
        raise HTTPException(e.status, e.detail) from e

    if row is not None:
        actor_label = row.name
    else:
        expected = (app_settings_service.read_all(db).external_api.token or "").strip()
        if expected and hmac.compare_digest(provided, expected):
            actor_label = "legacy-token"
        else:
            # 卡不认识：先记下「谁试的」（前 12 位），再区分是「没配卡」还是「卡不对」，
            # 否则用户拿到的一律是「未启用」，会误以为功能没打开。
            if request is not None:
                request.state.audit_actor = {
                    "type": "token",
                    "label": f"invalid:{provided[:12]}",
                }
            from app.models.api_token import ApiToken

            configured = bool(expected) or db.scalar(select(ApiToken.id).limit(1)) is not None
            if not configured:
                raise HTTPException(403, "还没有可用的 API 令牌（设置 · API 令牌）")
            raise HTTPException(401, "X-API-Token 不正确或已失效")

    # 先把归属写进 request.state：即使下面因权限不足被拒，
    # 审计也能记下「是哪个卡试的」，而不是 anonymous。
    if request is not None:
        request.state.audit_actor = {"type": "token", "label": actor_label}

    if row is not None and request is not None:
        need = api_token_service.required_scope(request.method, request.url.path)
        if not api_token_service.has_scope(row, need):
            raise HTTPException(
                403,
                f"令牌「{row.name}」没有 {need} 权限（当前：{'/'.join(api_token_service.split_scopes(row))}）",
            )

    user = db.scalar(
        select(User)
        .where(User.deleted_at.is_(None), User.is_active.is_(True))
        .order_by(User.id)
        .limit(1)
    )
    if user is None:
        raise HTTPException(403, "无可用账号")
    return CurrentUser(
        id=user.id,
        uid=user.uid,
        username=user.username,
        display_name=user.display_name or user.username,
        role=user.role,
        auth_session_pk=0,  # 外部 Token 无会话行
    )


def require_login(
    request: Request, db: Session = Depends(get_db)
) -> Optional[CurrentUser]:
    if is_public(request.method, request.url.path) or auth_is_off():
        return getattr(request.state, "current_user", None)
    # 签名播放 URL：原生播放器无 Cookie，凭 ?exp=&sig= 放行媒体路径
    from app.core import playback_sign

    if playback_sign.verify_request(request.url.path, request.query_params):
        return getattr(request.state, "current_user", None)
    # 外部集成 Token：库内容管理路由的替代凭据(不创建会话,不挤占 Web 登录)
    token_header = (request.headers.get("x-api-token") or "").strip()
    if token_header and is_token_route(request.url.path):
        return get_external_principal(db, token_header, request)
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
