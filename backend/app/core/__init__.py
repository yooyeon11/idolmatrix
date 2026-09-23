"""核心模块：配置、数据库、依赖注入、FFmpeg 自检。"""

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine, get_db, init_db
from app.core.ffmpeg import check_available, resolve_binaries

__all__ = [
    "settings",
    "Base",
    "SessionLocal",
    "engine",
    "get_db",
    "init_db",
    "check_available",
    "resolve_binaries",
]
