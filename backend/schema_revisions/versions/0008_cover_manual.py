"""视频封面：用户手动选帧标记。

Revision ID: 0008_cover_manual
Revises: 0007_photo_author
Create Date: 2026-08-26
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0008_cover_manual"
down_revision: Union[str, Sequence[str], None] = "0007_photo_author"
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
    _add_column(bind, "music_videos", "cover_manual", "cover_manual BOOLEAN NOT NULL DEFAULT 0")


def downgrade() -> None:
    return
