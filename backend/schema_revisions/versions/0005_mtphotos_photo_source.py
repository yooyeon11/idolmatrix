"""照片来源支持 MT Photos 相册。

Revision ID: 0005_mtphotos_photo_source
Revises: 0004_watch_plays
Create Date: 2026-08-24
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0005_mtphotos_photo_source"
down_revision: Union[str, Sequence[str], None] = "0004_watch_plays"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column(bind, table: str, name: str, ddl: str) -> None:
    insp = inspect(bind)
    if table not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns(table)}
    if name in existing:
        return
    bind.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))


def upgrade() -> None:
    bind = op.get_bind()
    _add_column(bind, "photo_sources", "provider", "provider VARCHAR(20) NOT NULL DEFAULT 'folder'")
    _add_column(bind, "photo_sources", "external_id", "external_id VARCHAR(80)")


def downgrade() -> None:
    # SQLite 不便删列，保留即可。
    return
