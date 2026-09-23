"""请求体上限与上传读取封顶。"""

from __future__ import annotations

from fastapi import HTTPException, UploadFile
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings

_READ_CHUNK = 1024 * 1024


class BodyLimitMiddleware(BaseHTTPMiddleware):
    """按 Content-Length 拒绝过大请求。无该头则交给后续读取封顶。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)
        ctype = (request.headers.get("content-type") or "").lower()
        if ctype.startswith("multipart/"):
            limit = int(settings.max_upload_body_bytes)
        else:
            limit = int(settings.max_json_body_bytes)
        raw = request.headers.get("content-length")
        if raw:
            try:
                size = int(raw)
            except ValueError:
                size = 0
            if size > limit:
                return JSONResponse({"detail": "请求体过大"}, status_code=413)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """基础安全头。HSTS 仅在 HTTPS（含可信反代的 X-Forwarded-Proto）时加。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        from app.core.security import cookie_secure

        if cookie_secure(request):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


async def read_upload_capped(file: UploadFile, max_bytes: int) -> bytes:
    """边读边截断，避免先整文件进内存再检查大小。"""
    buf = bytearray()
    while True:
        chunk = await file.read(_READ_CHUNK)
        if not chunk:
            break
        if len(buf) + len(chunk) > max_bytes:
            raise HTTPException(
                413, f"文件过大，上限 {max(1, max_bytes // (1024 * 1024))}MB"
            )
        buf.extend(chunk)
    return bytes(buf)
