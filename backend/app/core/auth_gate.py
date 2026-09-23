"""ASGI 登录门禁：只拦 /api，静态 SPA 放行。"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.database import SessionLocal
from app.core.deps import is_public, skip_session_touch
from app.core.security import COOKIE_NAME, csrf_origin_allowed
from app.services.auth_service import auth_is_off, load_session

logger = logging.getLogger("app.auth")

_WRITE = {"POST", "PUT", "PATCH", "DELETE"}


class AuthGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        if path != "/api" and not path.startswith("/api/"):
            return await call_next(request)
        if is_public(request.method, path):
            return await call_next(request)
        if auth_is_off():
            return await call_next(request)
        if request.method.upper() in _WRITE and not csrf_origin_allowed(request):
            return JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)
        token = request.cookies.get(COOKIE_NAME) or ""
        db = SessionLocal()
        try:
            user = load_session(
                db, token, touch=not skip_session_touch(request.method, path)
            )
            if user is None:
                return JSONResponse({"detail": "未登录"}, status_code=401)
            request.state.current_user = user
        finally:
            db.close()
        return await call_next(request)
