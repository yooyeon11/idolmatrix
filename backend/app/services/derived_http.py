"""派生图片的 HTTP 响应工具：ETag 与条件请求（304）协商。

FileResponse 自身不处理 If-None-Match / If-Modified-Since，每次请求都全量重传。
本模块为 derived 目录图片端点补齐协商逻辑：
- Cache-Control: public, max-age=0, must-revalidate（每次展示都重新协商，杜绝脏缓存）
- 强 ETag 由 mtime_ns + size 生成，文件被任何写方替换后必然变化
- 命中协商条件返回 304（携带 ETag / Cache-Control / Last-Modified），不再重传正文
"""

from __future__ import annotations

import email.utils
from datetime import timezone
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, Response


def _http_date(st_mtime: float) -> str:
    return email.utils.formatdate(st_mtime, usegmt=True)


# 派生图默认的缓存语义：每次展示都重新协商（杜绝脏缓存），命中协商条件返回 304。
# 需要保持「仅私有缓存」语义的调用方（如带登录门禁的视频封面）传 cache_control 覆盖。
DEFAULT_CACHE_CONTROL = "public, max-age=0, must-revalidate"


def serve_derived_image(
    request: Request,
    path: Path,
    media_type: str,
    *,
    cache_control: str = DEFAULT_CACHE_CONTROL,
) -> Response:
    """带缓存协商的图片响应：命中 If-None-Match / If-Modified-Since 返回 304。"""
    try:
        st = path.stat()
    except OSError:
        return FileResponse(path, media_type=media_type)
    etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
    common = {
        "Cache-Control": cache_control,
        "ETag": etag,
        "Last-Modified": _http_date(st.st_mtime),
    }
    inm = request.headers.get("if-none-match")
    if inm is not None:
        candidates = {c.strip() for c in inm.split(",") if c.strip()}
        if etag in candidates or "*" in candidates:
            return Response(status_code=304, headers=common)
    ims = request.headers.get("if-modified-since")
    if inm is None and ims:
        try:
            since = email.utils.parsedate_to_datetime(ims)
        except (TypeError, ValueError):
            since = None
        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            served = email.utils.parsedate_to_datetime(_http_date(st.st_mtime))
            if served <= since:
                return Response(status_code=304, headers=common)
    return FileResponse(path, media_type=media_type, headers=common)
