"""播放流 URL 签名。

原生播放器（VLC / ExoPlayer / AVPlayer / libmpv）没法带 Cookie，也不方便带
自定义头，所以 /stream、/manifest.m3u8、/segments/* 这三个媒体路径支持
「短时效签名 URL」：`?exp=<unix>&sig=<hmac>`。

- 签名只绑定 session_id + 过期时间，不绑定具体路径 —— 同一个 query 可以直接
  拼到清单里的分片 URL 上，播放器按清单逐片拉取时天然带签名。
- 密钥优先取环境变量 PLAYBACK_URL_SECRET；没有则在数据库同级目录持久化一个
  随机密钥文件（0600）。这样重启/升级后已发出的 URL 不会立刻失效，也不会因为
  随机密钥导致多实例校验不一致。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TTL = 6 * 3600  # 6 小时：够一部电影 + 暂停
_secret_cache: bytes | None = None


def _secret_path() -> Path:
    """密钥落盘位置：优先环境变量，其次容器卷 /data，最后 ./data。

    放在卷上才能跨「容器重建」保持稳定，否则每次重建都会换密钥、
    已发出的播放 URL 立刻失效（虽然只影响 6 小时内，但很烦）。
    """
    env = (os.environ.get("PLAYBACK_URL_SECRET_FILE") or "").strip()
    if env:
        return Path(env)
    for cand in (Path("/data"), Path("./data")):
        try:
            if cand.is_dir() and os.access(cand, os.W_OK):
                return cand / ".playback_url_secret"
        except OSError:
            continue
    return Path("./data/.playback_url_secret")


def _secret() -> bytes:
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache

    env = (os.environ.get("PLAYBACK_URL_SECRET") or "").strip()
    if env:
        _secret_cache = env.encode("utf-8")
        return _secret_cache

    path = _secret_path()
    try:
        if path.is_file():
            raw = path.read_bytes().strip()
            if len(raw) >= 16:
                _secret_cache = raw
                return _secret_cache
        data = secrets.token_hex(32).encode("ascii")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        _secret_cache = data
        logger.info("已生成播放 URL 签名密钥：%s", path)
        return data
    except OSError as exc:  # 只读卷等场景：退化为进程内随机密钥（重启后旧 URL 失效）
        logger.warning("播放 URL 签名密钥无法持久化，改用进程内临时密钥：%s", exc)
        _secret_cache = secrets.token_hex(32).encode("ascii")
        return _secret_cache


def _digest(session_id: int, exp: int) -> str:
    msg = f"{int(session_id)}:{int(exp)}".encode("ascii")
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:32]


def sign(session_id: int, ttl: int = DEFAULT_TTL) -> tuple[int, str]:
    """返回 (exp, sig)。"""
    exp = int(time.time()) + int(ttl)
    return exp, _digest(session_id, exp)


def query(session_id: int, ttl: int = DEFAULT_TTL) -> str:
    """拼在播放 URL 后的 `?exp=..&sig=..`。"""
    exp, sig = sign(session_id, ttl)
    return f"?exp={exp}&sig={sig}"


def verify(session_id: int, exp: str | int | None, sig: str | None) -> bool:
    if not exp or not sig:
        return False
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_i < int(time.time()):
        return False
    return hmac.compare_digest(_digest(session_id, exp_i), str(sig))


def verify_request(path: str, query_params) -> bool:
    """从请求路径 + query 里判断是否为合法的签名播放请求。

    只放行媒体路径，已创建会话的增删改仍必须走登录态。
    """
    import re

    # ⚠ compat.mp4 / progressive.mp4 必须在名单里：国产内核（vivo/OPPO/小米/华为）
    # 会把 <video> 劫持给**系统原生播放器**，那是独立进程、不带浏览器 Cookie。
    # 只靠登录态的媒体地址在它手里必然 401，页面表现就是「视频播放失败，可尝试
    # 以下操作 / 刷新网页」——而这段文案来自厂商播放器，不是我们的代码。
    m = re.match(
        r"^/api/playback/sessions/(\d+)/(?:stream|manifest\.m3u8|index\.m3u8"
        r"|compat\.mp4|progressive\.mp4|segments/[^/]+)$",
        path,
    )
    if not m:
        return False
    try:
        sid = int(m.group(1))
    except ValueError:
        return False
    return verify(sid, query_params.get("exp"), query_params.get("sig"))
