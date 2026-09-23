"""SQLAlchemy 2.0 声明式数据库引擎与会话管理。

默认 SQLite，DATABASE_URL 改为 PostgreSQL 即可切换，业务代码无感知。
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from pathlib import Path
from typing import Annotated

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import PROJECT_ROOT, settings

logger = logging.getLogger(__name__)

# SQLite 等锁的最长时间（毫秒）：NAS 上并发写（心跳 + ffmpeg 回调 + 入库）
# 不设该值时立即抛 "database is locked"
SQLITE_BUSY_TIMEOUT_MS = 5000


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    pass


def _enable_sqlite_pragmas(engine) -> None:
    """为 SQLite 连接设置关键 PRAGMA（每个连接生效，需挂在 connect 事件上）。

    - journal_mode=WAL：读写不互相阻塞，写不再卡住全部读（NAS 场景必须）
    - busy_timeout：遇锁等待而非立即报错
    - foreign_keys：启用外键约束，模型声明的 ondelete CASCADE/SET NULL
      只有开启后才会被 SQLite 执行（否则级联全靠 ORM 兜底）
    - synchronous=NORMAL：WAL 下的推荐档位，兼顾性能与掉电安全
    """

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


def _resolve_database_url(url: str) -> str:
    """把 sqlite 相对路径锚定到项目根。

    `sqlite:///./data/...` 默认按进程启动时 CWD 解析：从不同目录启动
    （backend/ vs 项目根 vs Docker WORKDIR）会静默使用不同库文件，
    表现为「数据全丢」。统一按 PROJECT_ROOT 解析，与 settings.db_path
    口径一致；绝对路径（Docker 的 /data/db/...）不受影响。
    """
    prefix = "sqlite:///"
    if url.startswith(prefix):
        path = url[len(prefix):]
        if path and not Path(path).is_absolute():
            return prefix + str((PROJECT_ROOT / path).resolve())
    return url


def _build_engine():
    url = _resolve_database_url(settings.database_url)
    connect_args = {}
    is_sqlite = url.startswith("sqlite")
    if is_sqlite:
        # SQLite 需要关闭线程检查以支持多线程请求
        connect_args = {"check_same_thread": False}
    eng = create_engine(
        url,
        connect_args=connect_args,
        echo=settings.debug,
        future=True,
    )
    if is_sqlite:
        _enable_sqlite_pragmas(eng)
    return eng


engine = _build_engine()
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


def init_db() -> None:
    """建表 + Alembic upgrade + 列补齐安全网。

    create_all 负责全新空库；已有库的列/索引补丁走 alembic/versions。
    _ensure_missing_columns 作为安全网，防止 Alembic 版本表与实际 schema
    不一致时遗漏列（从旧版升级时的典型问题）。数据回填仍在此处。
    """
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _run_alembic()
    _ensure_missing_columns()
    _backfill_video_tracks()
    _backfill_video_types()


def _ensure_missing_columns() -> None:
    """直接 ALTER TABLE 补齐模型有但 DB 缺的列。

    Alembic 版本表可能记录"已迁移到最新"但实际列未补上——例如从旧版
    升级时 alembic_version 已存在，迁移被判定为已完成而跳过，列却
    从未真正添加。此函数遍历所有模型表，对缺失列直接 ALTER TABLE。
    """
    from sqlalchemy import inspect, text

    try:
        insp = inspect(engine)
        db_tables = set(insp.get_table_names())
        patched = 0
        for table_name, table in Base.metadata.tables.items():
            if table_name not in db_tables:
                continue
            existing_cols = {c["name"] for c in insp.get_columns(table_name)}
            for col in table.columns:
                if col.name in existing_cols:
                    continue
                col_type = col.type.compile(dialect=engine.dialect)
                ddl = f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type}"
                try:
                    with engine.begin() as conn:
                        conn.execute(text(ddl))
                    logger.info("已补充列: %s.%s (%s)", table_name, col.name, col_type)
                    patched += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning("补充列 %s.%s 失败: %s", table_name, col.name, e)
        if patched:
            logger.info("安全网补齐了 %d 个缺失列", patched)
    except Exception as e:  # noqa: BLE001
        logger.warning("补列安全网执行失败: %s", e)


def _alembic_config():
    from alembic.config import Config

    backend_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "schema_revisions"))
    return cfg


def _run_alembic() -> None:
    from alembic import command

    try:
        command.upgrade(_alembic_config(), "head")
    except Exception as e:  # noqa: BLE001
        logger.warning("Alembic upgrade 失败：%s", e)
        raise


def _backfill_video_tracks() -> None:
    """把旧的 music_video_songs 填进有序曲目表。"""
    from sqlalchemy import select

    from app.models.music_video import MusicVideo, MusicVideoTrack

    try:
        db = SessionLocal()
        try:
            videos = db.scalars(select(MusicVideo)).all()
            created = 0
            for mv in videos:
                if mv.video_tracks:
                    continue
                songs = list(mv.songs or [])
                if not songs and mv.song_id:
                    from app.models.song import Song

                    s = db.get(Song, mv.song_id)
                    if s is not None:
                        songs = [s]
                for i, song in enumerate(songs):
                    db.add(
                        MusicVideoTrack(
                            music_video_id=mv.id, song_id=song.id, position=i
                        )
                    )
                    created += 1
            if created:
                db.commit()
                logger.info("已回填 music_video_tracks %s 行", created)
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("回填曲目表失败：%s", e)


def _backfill_video_types() -> None:
    """旧行只有 video_type 时，把 video_types 写成 [video_type]。"""
    from sqlalchemy import select

    from app.models.music_video import MusicVideo
    from app.services.video_meta import normalize_video_types

    try:
        db = SessionLocal()
        try:
            changed = 0
            for mv in db.scalars(select(MusicVideo)).all():
                primary, types = normalize_video_types(mv.video_types, mv.video_type)
                if mv.video_type != primary or list(mv.video_types or []) != types:
                    mv.video_type = primary
                    mv.video_types = types
                    changed += 1
                tracks = list(mv.video_tracks or [])
                if tracks:
                    first = sorted(tracks, key=lambda r: r.position)[0].song_id
                    if mv.song_id != first:
                        mv.song_id = first
                        changed += 1
            if changed:
                db.commit()
                logger.info("已回填 video_types / song_id %s 行", changed)
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("回填 video_types 失败：%s", e)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每请求一个 Session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 便于在路由中直接做类型注解
DBSession = Annotated[Session, "depends(get_db)"]
