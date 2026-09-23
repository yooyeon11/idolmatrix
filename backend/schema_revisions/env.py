"""Alembic 环境：复用应用自己的 engine / metadata。

应用进程内已用 basicConfig 配好日志；alembic.ini 的 fileConfig 会禁用
所有已存在的 logger 并把 root 重置为 WARN，吞掉迁移之后的全部应用日志。
因此仅在独立运行 alembic CLI（root 还没有任何 handler）时才套用它。
"""

from __future__ import annotations

import logging
from logging.config import fileConfig

from alembic import context

from app.core.database import Base, engine
import app.models  # noqa: F401

config = context.config
if config.config_file_name is not None and not logging.getLogger().handlers:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = engine.url.render_as_string(hide_password=False)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
