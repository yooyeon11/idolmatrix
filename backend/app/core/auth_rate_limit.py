"""进程内登录/bootstrap 失败限流。单 worker 假设。"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict

from app.core.config import settings

_lock = threading.Lock()
_hits: OrderedDict[str, list[float]] = OrderedDict()
_MAX_KEYS = 1024


def _key(ip: str, username: str) -> str:
    raw = f"{ip}\0{username.lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def too_many_failures(ip: str, username: str) -> tuple[bool, int]:
    """返回 (是否超限, Retry-After 秒)。"""
    now = time.time()
    window = float(settings.auth_login_window_seconds)
    max_fail = int(settings.auth_login_max_failures)
    k = _key(ip or "unknown", username or "")
    with _lock:
        stamps = [t for t in _hits.get(k, []) if now - t < window]
        if stamps:
            _hits[k] = stamps
            _hits.move_to_end(k)
        else:
            _hits.pop(k, None)
        if len(stamps) >= max_fail:
            retry = max(1, int(window - (now - stamps[0])))
            return True, retry
        return False, 0


def record_failure(ip: str, username: str) -> None:
    now = time.time()
    window = float(settings.auth_login_window_seconds)
    k = _key(ip or "unknown", username or "")
    with _lock:
        stamps = [t for t in _hits.get(k, []) if now - t < window]
        stamps.append(now)
        _hits[k] = stamps
        _hits.move_to_end(k)
        while len(_hits) > _MAX_KEYS:
            _hits.popitem(last=False)


def clear_failures(ip: str, username: str) -> None:
    k = _key(ip or "unknown", username or "")
    with _lock:
        _hits.pop(k, None)
