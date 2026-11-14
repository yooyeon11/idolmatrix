"""只读镜像门禁：MIRROR_MODE 下拦掉一切写请求。

为什么需要它，而不是「按模式不挂某些 router」：
写接口散落在 music_videos / artists / groups / photos / app_settings …
十几个模块里，逐个条件挂载既容易漏，也会让路由表随环境变化（同一份代码
两种 API 形状，前端很难处理）。统一在 ASGI 层拦一次，行为恒定且可观测。

放行的写接口只有三类，缺一不可：
1. `/api/auth/*`        —— 登录/登出（在线版仍要门禁）
2. `/api/playback/sessions*` —— 建会话、心跳、停止（播放状态机必须能写）
3. `/api/mirror/*`      —— 镜像自检/重载

其余 POST/PUT/PATCH/DELETE 一律 403，并明确告知「这是只读镜像」，
避免前端以为是自己拼错了 URL。
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings

logger = logging.getLogger("app.mirror")

_WRITE = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# 镜像模式下仍然允许的写前缀（按 path.startswith 匹配）
_WRITE_ALLOW_PREFIXES = (
    "/api/auth/",
    "/api/playback/sessions",
    "/api/mirror/",
)


def _is_write_allowed(path: str) -> bool:
    return any(path.startswith(p) for p in _WRITE_ALLOW_PREFIXES)


class MirrorReadOnlyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if not settings.mirror_mode:
            return await call_next(request)

        if request.method.upper() in _WRITE and request.url.path.startswith("/api/"):
            if not _is_write_allowed(request.url.path):
                logger.info(
                    "只读镜像已拦截写请求: %s %s", request.method, request.url.path
                )
                return JSONResponse(
                    {
                        "detail": "这是只读镜像站点，不支持修改数据；请在本地的 idolMatrix 里操作。",
                        "mirror": True,
                        "path": request.url.path,
                    },
                    status_code=403,
                )
        return await call_next(request)
