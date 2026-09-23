"""登录、bootstrap、session、启动钩子。"""

from __future__ import annotations

import logging
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth_rate_limit import clear_failures, record_failure, too_many_failures
from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    USERNAME_RE,
    hash_password,
    hash_token,
    new_session_token,
    password_fingerprint,
    verify_password,
)
from app.models.app_setting import AppSetting
from app.models.auth_session import AuthSession
from app.models.user import User

logger = logging.getLogger("app.auth")

RESET_FINGERPRINT_KEY = "auth_reset_consumed"
MAX_SESSIONS_PER_USER = 10
USERNAME_PATTERN = re.compile(USERNAME_RE)
TOUCH_THROTTLE = timedelta(minutes=10)

_token_cache: dict[str, tuple[int, float]] = {}
_CACHE_TTL = 60.0


class AuthError(Exception):
    def __init__(self, status: int, detail: str, retry_after: int = 0):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.retry_after = retry_after


@dataclass
class CurrentUser:
    id: int
    uid: str
    username: str
    display_name: str
    role: str
    auth_session_pk: int


def auth_is_off() -> bool:
    return (settings.auth_required or "auto").strip().lower() == "false"


def count_users(db: Optional[Session] = None) -> int:
    close = False
    if db is None:
        db = SessionLocal()
        close = True
    try:
        return int(
            db.scalar(
                select(func.count(User.id)).where(User.deleted_at.is_(None))
            )
            or 0
        )
    finally:
        if close:
            db.close()


def validate_username(username: str) -> str:
    text_u = (username or "").strip()
    if not text_u or not USERNAME_PATTERN.match(text_u) or not (3 <= len(text_u) <= 32):
        raise AuthError(400, "用户名须为 3–32 位字母、数字、点、下划线或短横，且以字母或数字开头")
    return text_u


def validate_password(password: str) -> str:
    if not isinstance(password, str) or not (8 <= len(password) <= 128):
        raise AuthError(400, "密码长度须为 8–128 位")
    return password


def _utcnow() -> datetime:
    return datetime.utcnow()


def _drop_token_cache(token_hash: Optional[str] = None, user_id: Optional[int] = None) -> None:
    if token_hash:
        _token_cache.pop(token_hash, None)
    if user_id is not None:
        dead = [k for k, (uid, _exp) in _token_cache.items() if uid == user_id]
        for k in dead:
            _token_cache.pop(k, None)


def cache_user_id(token_hash: str, user_id: int) -> None:
    import time

    _token_cache[token_hash] = (user_id, time.time() + _CACHE_TTL)


def cached_user_id(token_hash: str) -> Optional[int]:
    import time

    hit = _token_cache.get(token_hash)
    if not hit:
        return None
    uid, exp = hit
    if exp < time.time():
        _token_cache.pop(token_hash, None)
        return None
    return uid


def _trim_sessions(db: Session, user_id: int) -> None:
    rows = list(
        db.scalars(
            select(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
            )
            .order_by(AuthSession.last_seen_at.desc())
        ).all()
    )
    extra = rows[MAX_SESSIONS_PER_USER:]
    now = _utcnow()
    for row in extra:
        row.revoked_at = now
        _drop_token_cache(row.token_hash)


def mint_session(
    db: Session,
    user: User,
    *,
    user_agent: str = "",
    ip: str = "",
) -> str:
    now = _utcnow()
    token = new_session_token()
    row = AuthSession(
        user_id=user.id,
        token_hash=hash_token(token),
        user_agent=(user_agent or "")[:400] or None,
        ip=(ip or "")[:64] or None,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(seconds=settings.auth_absolute_seconds),
        revoked_at=None,
    )
    db.add(row)
    db.flush()
    _trim_sessions(db, user.id)
    return token


def revoke_session(db: Session, session_row: AuthSession) -> None:
    session_row.revoked_at = _utcnow()
    _drop_token_cache(session_row.token_hash)


def revoke_all_sessions(db: Session, user_id: int) -> int:
    now = _utcnow()
    rows = list(
        db.scalars(
            select(AuthSession).where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
            )
        ).all()
    )
    for row in rows:
        row.revoked_at = now
    _drop_token_cache(user_id=user_id)
    return len(rows)


def _session_alive(row: AuthSession, now: datetime) -> bool:
    if row.revoked_at is not None:
        return False
    if row.expires_at <= now:
        return False
    idle = timedelta(seconds=settings.auth_idle_seconds)
    if row.last_seen_at + idle <= now:
        return False
    return True


def load_session(db: Session, token: str, *, touch: bool) -> Optional[CurrentUser]:
    if not token:
        return None
    th = hash_token(token)
    now = _utcnow()
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == th))
    if row is None or not _session_alive(row, now):
        return None
    user = db.get(User, row.user_id)
    if user is None or user.deleted_at is not None or not user.is_active:
        return None
    if touch and now - row.last_seen_at >= TOUCH_THROTTLE:
        row.last_seen_at = now
    return CurrentUser(
        id=user.id,
        uid=user.uid,
        username=user.username,
        display_name=user.display_name or user.username,
        role=user.role,
        auth_session_pk=row.id,
    )


def login(db: Session, username: str, password: str, *, ip: str, user_agent: str) -> tuple[User, str]:
    name = (username or "").strip()
    limited, retry = too_many_failures(ip, name)
    if limited:
        raise AuthError(429, "尝试次数过多，请稍后再试", retry_after=retry)
    user = db.scalar(
        select(User).where(
            func.lower(User.username) == name.lower(),
            User.deleted_at.is_(None),
        )
    )
    hashed = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    ok = verify_password(password or "", hashed)
    if user is None or not user.is_active or not ok:
        record_failure(ip, name)
        raise AuthError(401, "用户名或密码不正确")
    clear_failures(ip, name)
    user.last_login_at = _utcnow()
    token = mint_session(db, user, user_agent=user_agent, ip=ip)
    db.commit()
    logger.info("login ok user=%s", user.username)
    return user, token


def username_taken(db: Session, username: str, *, exclude_id: Optional[int] = None) -> bool:
    q = select(User.id).where(
        func.lower(User.username) == username.lower(),
        User.deleted_at.is_(None),
    )
    if exclude_id is not None:
        q = q.where(User.id != exclude_id)
    return db.scalar(q) is not None


def require_owner(actor: CurrentUser, detail: str = "仅主账号可以执行此操作") -> None:
    if actor.role != "owner":
        raise AuthError(403, detail)


def change_username(db: Session, user: User, new_username: str) -> User:
    name = validate_username(new_username)
    if name.lower() == (user.username or "").lower():
        if name != user.username:
            user.username = name
            db.commit()
            db.refresh(user)
        return user
    if username_taken(db, name, exclude_id=user.id):
        raise AuthError(409, "用户名已被占用")
    user.username = name
    db.commit()
    db.refresh(user)
    logger.info("username changed user_id=%s name=%s", user.id, name)
    return user


def list_accounts(db: Session, actor: CurrentUser) -> list[User]:
    require_owner(actor)
    return list(
        db.scalars(
            select(User).where(User.deleted_at.is_(None)).order_by(User.id.asc())
        ).all()
    )


def create_agent(
    db: Session,
    actor: CurrentUser,
    username: str,
    password: str,
    display_name: str = "",
) -> User:
    require_owner(actor)
    username = validate_username(username)
    password = validate_password(password)
    if username_taken(db, username):
        raise AuthError(409, "用户名已被占用")
    display = (display_name or "").strip() or username
    if len(display) > 80:
        raise AuthError(400, "显示名过长")
    row = User(
        username=username,
        display_name=display,
        password_hash=hash_password(password),
        role="agent",
        is_active=True,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AuthError(409, "用户名已被占用")
    db.refresh(row)
    logger.info("agent created by=%s user=%s", actor.username, username)
    return row


def delete_agent(db: Session, actor: CurrentUser, user_id: int) -> None:
    require_owner(actor)
    row = db.get(User, user_id)
    if row is None or row.deleted_at is not None:
        raise AuthError(404, "账号不存在")
    if row.role == "owner":
        raise AuthError(400, "不能删除主账号")
    if row.id == actor.id:
        raise AuthError(400, "不能删除当前登录账号")
    row.soft_delete()
    row.is_active = False
    revoke_all_sessions(db, row.id)
    db.commit()
    logger.info("agent deleted by=%s user_id=%s", actor.username, user_id)


def change_password(db: Session, user: User, old: str, new: str, *, ip: str, user_agent: str) -> str:
    if not verify_password(old or "", user.password_hash):
        raise AuthError(400, "当前密码不正确")
    new = validate_password(new)
    user.password_hash = hash_password(new)
    revoke_all_sessions(db, user.id)
    token = mint_session(db, user, user_agent=user_agent, ip=ip)
    db.commit()
    logger.info("password changed user=%s", user.username)
    return token


def bootstrap_owner(
    username: str,
    password: str,
    display_name: str,
    *,
    token: str,
    ip: str,
    user_agent: str,
) -> tuple[int, str]:
    limited, retry = too_many_failures(ip, "bootstrap")
    if limited:
        raise AuthError(429, "尝试次数过多，请稍后再试", retry_after=retry)
    expected = (settings.bootstrap_token or "").strip()
    provided = (token or "").strip()
    if expected:
        if not provided or not secrets.compare_digest(provided, expected):
            record_failure(ip, "bootstrap")
            raise AuthError(403, "初始化口令不正确")
    else:
        from app.core.security import is_private_client_ip

        if not is_private_client_ip(ip):
            record_failure(ip, "bootstrap")
            raise AuthError(
                403,
                "公网初始化必须在服务器配置 BOOTSTRAP_TOKEN，建号后请删除该变量",
            )
    username = validate_username(username)
    password = validate_password(password)
    display = (display_name or "").strip() or username
    if len(display) > 80:
        raise AuthError(400, "显示名过长")
    now = _utcnow()
    uid = secrets.token_hex(16)
    # UUID format
    import uuid as _uuid

    uid = str(_uuid.uuid4())
    pw_hash = hash_password(password)
    session_token = new_session_token()
    th = hash_token(session_token)
    expires = now + timedelta(seconds=settings.auth_absolute_seconds)
    raw = engine.raw_connection()
    try:
        raw.execute("BEGIN IMMEDIATE")
        n = raw.execute(
            "SELECT COUNT(*) FROM users WHERE deleted_at IS NULL"
        ).fetchone()[0]
        if n:
            raw.execute("ROLLBACK")
            raise AuthError(403, "已完成初始化")
        raw.execute(
            """
            INSERT INTO users (
                uid, deleted_at, created_at, updated_at,
                username, display_name, password_hash, role, is_active, last_login_at
            ) VALUES (?, NULL, ?, ?, ?, ?, ?, 'owner', 1, ?)
            """,
            (uid, now, now, username, display, pw_hash, now),
        )
        user_id = raw.execute("SELECT last_insert_rowid()").fetchone()[0]
        raw.execute(
            """
            INSERT INTO auth_sessions (
                user_id, token_hash, user_agent, ip,
                created_at, last_seen_at, expires_at, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                user_id,
                th,
                (user_agent or "")[:400] or None,
                (ip or "")[:64] or None,
                now,
                now,
                expires,
            ),
        )
        raw.commit()
        logger.info("bootstrap owner user=%s", username)
        return int(user_id), session_token
    except AuthError:
        raise
    except Exception:
        try:
            raw.rollback()
        except Exception:
            pass
        raise
    finally:
        raw.close()


def _get_reset_fingerprint(db: Session) -> str:
    row = db.get(AppSetting, RESET_FINGERPRINT_KEY)
    if row is None or not isinstance(row.value, str):
        return ""
    return row.value


def _set_reset_fingerprint(db: Session, value: str) -> None:
    row = db.get(AppSetting, RESET_FINGERPRINT_KEY)
    if row is None:
        db.add(AppSetting(key=RESET_FINGERPRINT_KEY, value=value))
    else:
        row.value = value


def clear_reset_fingerprint() -> None:
    db = SessionLocal()
    try:
        row = db.get(AppSetting, RESET_FINGERPRINT_KEY)
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()


def reset_owner_password(new_password: str) -> None:
    new_password = validate_password(new_password)
    db = SessionLocal()
    try:
        owner = db.scalar(
            select(User)
            .where(User.role == "owner")
            .order_by(User.id.asc())
        )
        if owner is None:
            raise AuthError(
                400,
                "没有可重置的 owner。请用 BOOTSTRAP_OWNER_PASSWORD 或打开 /setup。",
            )
        owner.password_hash = hash_password(new_password)
        owner.is_active = True
        owner.deleted_at = None
        revoke_all_sessions(db, owner.id)
        db.commit()
        logger.warning("owner password reset via CLI user=%s", owner.username)
    finally:
        db.close()


def apply_startup_auth() -> None:
    """init_db 之后：reset 环境变量 → 无头 bootstrap。顺序不能对调。"""
    reset = (settings.auth_reset_owner_password or "").strip()
    bootstrap_pw = (settings.bootstrap_owner_password or "").strip()
    db = SessionLocal()
    try:
        owners = list(
            db.scalars(select(User).where(User.role == "owner")).all()
        )
        if reset:
            if not owners:
                logger.error(
                    "没有可重置的 owner。请用 BOOTSTRAP_OWNER_PASSWORD 或打开 /setup。"
                )
                sys.exit(1)
            fp = password_fingerprint(reset)
            consumed = _get_reset_fingerprint(db)
            if consumed and consumed == fp:
                logger.error("AUTH_RESET_OWNER_PASSWORD 已使用过且未删除，拒绝启动")
                sys.exit(1)
            hashed = hash_password(reset)
            for owner in owners:
                owner.password_hash = hashed
                owner.is_active = True
                owner.deleted_at = None
                revoke_all_sessions(db, owner.id)
            _set_reset_fingerprint(db, fp)
            db.commit()
            logger.warning("AUTH_RESET_OWNER_PASSWORD 已重置全部 owner 密码，请从 compose 删除该变量后重启")
            return

        if count_users(db) == 0 and not (settings.bootstrap_token or "").strip() and not bootstrap_pw:
            logger.warning(
                "尚未创建账号且未设置 BOOTSTRAP_TOKEN：局域网可打开 /setup；"
                "经公网 IP 的初始化会被拒绝"
            )

        if bootstrap_pw and count_users(db) == 0:
            username = validate_username(settings.bootstrap_owner_username or "owner")
            display = username
            bootstrap_owner(
                username,
                bootstrap_pw,
                display,
                token=settings.bootstrap_token or "",
                ip="127.0.0.1",
                user_agent="startup",
            )
            logger.warning("已用 BOOTSTRAP_OWNER_PASSWORD 创建 owner=%s，请删除该环境变量", username)
    except AuthError as e:
        logger.error("%s", e.detail)
        sys.exit(1)
    finally:
        db.close()


def cleanup_sessions(db: Session) -> int:
    now = _utcnow()
    idle_cut = now - timedelta(seconds=settings.auth_idle_seconds)
    rows = list(
        db.scalars(
            select(AuthSession).where(
                (AuthSession.revoked_at.is_not(None))
                | (AuthSession.expires_at < now)
                | (AuthSession.last_seen_at < idle_cut)
            )
        ).all()
    )
    n = 0
    for row in rows:
        if row.revoked_at is None:
            row.revoked_at = now
            n += 1
        _drop_token_cache(row.token_hash)
    if n:
        db.commit()
    return n
