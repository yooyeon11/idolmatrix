"""密码哈希、session token、Cookie 名。"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from urllib.parse import urlparse

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Request, Response

from app.core.cors_gate import cross_origin_writes_allowed

from app.core.config import settings

COOKIE_NAME = "im_session"

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_PASSWORD_HASH = _hasher.hash("dummy")

USERNAME_RE = r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$"


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain)
    except (VerifyMismatchError, InvalidHashError, ValueError, TypeError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def password_fingerprint(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def cookie_secure(request: Request) -> bool:
    mode = (settings.auth_cookie_secure or "auto").strip().lower()
    if mode in ("1", "true", "yes", "on"):
        return True
    if mode in ("0", "false", "no", "off"):
        return False
    if request.url.scheme == "https":
        return True
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if proto == "https":
        peer = request.client.host if request.client else ""
        trusted = [x.strip() for x in (settings.auth_trusted_proxies or "").split(",") if x.strip()]
        if peer and peer in trusted:
            return True
    return False


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.auth_absolute_seconds,
        httponly=True,
        samesite="lax",
        secure=cookie_secure(request),
        path="/",
    )


def clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def csrf_origin_allowed(request: Request) -> bool:
    """写操作 Origin/Referer 校验。两头都缺则拒绝。"""
    # 全局放行开关（设置页/environment 可切换）：开启则跳过写请求的来源校验
    if cross_origin_writes_allowed():
        return True
    origin = (request.headers.get("origin") or "").strip()
    if not origin:
        referer = (request.headers.get("referer") or "").strip()
        if not referer:
            return False
        parsed_ref = urlparse(referer)
        if not parsed_ref.scheme or not parsed_ref.netloc:
            return False
        origin = f"{parsed_ref.scheme}://{parsed_ref.netloc}"
    parsed = urlparse(origin)
    if not parsed.scheme or not parsed.netloc:
        return False
    host = (request.headers.get("host") or "").strip()
    if parsed.netloc == host:
        return True
    allowed = []
    for item in settings.cors_origins or []:
        text = str(item).strip()
        if not text:
            continue
        allowed.append(text.rstrip("/"))
        netloc = urlparse(text).netloc
        if netloc:
            allowed.append(netloc)
    candidate = origin.rstrip("/")
    return candidate in allowed or parsed.netloc in allowed


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _trusted_proxy_list() -> list[str]:
    return [
        x.strip()
        for x in (settings.auth_trusted_proxies or "").split(",")
        if x.strip()
    ]


def request_client_ip(request: Request) -> str:
    """连接对端 IP；仅当对端在 AUTH_TRUSTED_PROXIES 里才采信转发头。

    未配 trusted proxies 时与原先 request.client.host 一致，直连 NAS 不受影响。
    """
    peer = request.client.host if request.client else ""
    trusted = _trusted_proxy_list()
    if peer and trusted and peer in trusted:
        xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if xff:
            return xff
        real_ip = (request.headers.get("x-real-ip") or "").strip()
        if real_ip:
            return real_ip
    return peer or ""


def is_private_client_ip(ip: str) -> bool:
    """局域网 / 本机 / TestClient，用于无 BOOTSTRAP_TOKEN 时仍允许 /setup。"""
    text = (ip or "").strip().lower()
    if not text or text in ("testclient", "localhost"):
        return True
    if "%" in text:
        text = text.split("%", 1)[0]
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return False
    return bool(addr.is_private or addr.is_loopback or addr.is_link_local)


def is_kept_secret(value: object) -> bool:
    """PATCH 里空串 / 占位符表示「不改密钥」，避免脱敏回写把 Key 清掉。"""
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return True
    if text.lower() in ("<redacted>", "[redacted]", "********"):
        return True
    if set(text) <= set("*•"):
        return True
    return False
