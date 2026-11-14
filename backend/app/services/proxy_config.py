"""外部站点代理开关：所有出网请求（TheAudioDB/Deezer/iTunes/Wikidata、封面与头像下载）共用。

设置存在 SQLite app_setting（key=external_proxy：enabled/url），设置页可随时切换。
服务层拿不到 db session，这里做 TTL 缓存：每次 urlopen 前检查缓存是否过期，
过期则开短 session 读一次；读失败维持当前状态（默认关闭）。

注意：开关关闭时显式使用空 ProxyHandler，不继承环境变量 http_proxy/https_proxy，
行为可控可预期（NAS 容器里环境不可控）。
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_TTL_SECONDS = 15.0

_lock = threading.Lock()
_state: Dict[str, Any] = {"enabled": False, "url": ""}
_loaded_at: float = 0.0


def set_proxy(enabled: bool, url: str) -> None:
    """直接更新代理状态（设置写入后立即生效，绕过 TTL）。"""
    global _loaded_at
    with _lock:
        _state["enabled"] = bool(enabled)
        _state["url"] = (url or "").strip()
        _loaded_at = time.monotonic()


def current() -> Dict[str, Any]:
    """返回当前代理状态副本（供诊断/测试）。"""
    with _lock:
        return dict(_state)


def _in_container() -> bool:
    """是否在容器内运行。"""
    import os

    try:
        return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")
    except OSError:
        return False


def _normalize(url: str) -> str:
    """纠正「宿主机上解析不了 host.docker.internal」这个高频坑。

    设置里为了容器部署常填 http://host.docker.internal:7897，
    但宿主机直接跑后端时该名字不存在（getaddrinfo 失败），表现为
    「明明配了代理却没走代理」。非容器环境统一换成 127.0.0.1。
    """
    url = (url or "").strip()
    if url and "host.docker.internal" in url and not _in_container():
        return url.replace("host.docker.internal", "127.0.0.1")
    return url


def enabled_url() -> str:
    """当前生效的代理地址；未开启或未填地址返回空串。

    会先按 TTL 刷新一次（设置页改完立即 set_proxy，跨进程/冷启动走这里读库）。
    """
    _load_from_db()
    with _lock:
        enabled, url = _state["enabled"], _state["url"]
    return _normalize(url) if enabled else ""


def _load_from_db() -> None:
    global _loaded_at
    now = time.monotonic()
    if now - _loaded_at < _TTL_SECONDS:
        return
    try:
        # 延迟导入避免循环依赖
        from app.core.database import SessionLocal
        from app.models.app_setting import AppSetting

        with SessionLocal() as db:
            row = db.get(AppSetting, "external_proxy")
            blob = row.value if row is not None and isinstance(row.value, dict) else {}
        set_proxy(bool(blob.get("enabled")), str(blob.get("url") or ""))
    except Exception as e:  # noqa: BLE001
        # 读库失败（如启动初期建表未完成）不致命，维持当前状态并推迟下次重试
        logger.debug("[proxy] 读取设置失败，维持当前状态: %s", e)
        with _lock:
            _loaded_at = time.monotonic()


def _ssl_context():
    """TLS 上下文：优先用 certifi 的 CA 包。

    python.org 官方安装包在 macOS 上不带系统根证书（cert verify failed 是
    常见报告），certifi 可用时显式指定；不可用则退回默认行为。
    """
    global _ssl_ctx
    if _ssl_ctx is not None:
        return _ssl_ctx
    import ssl

    ctx = ssl.create_default_context()
    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        pass
    _ssl_ctx = ctx
    return ctx


_ssl_ctx: Any = None


def urlopen(req: urllib.request.Request, timeout: Optional[float] = None):
    """按代理设置打开请求；返回上下文管理器，用法同 urllib.request.urlopen。"""
    _load_from_db()
    with _lock:
        enabled, url = _state["enabled"], _state["url"]
    if enabled and url:
        url = _normalize(url)
        proxies: Dict[str, str] = {"http": url, "https": url}
    else:
        proxies = {}  # 显式空：不继承环境变量代理
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler(proxies),
        urllib.request.HTTPSHandler(context=_ssl_context()),
    )
    return opener.open(req, timeout=timeout)
