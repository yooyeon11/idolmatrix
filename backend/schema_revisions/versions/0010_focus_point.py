"""视频封面焦点：人脸居中裁切坐标。

Revision ID: 0010_focus_point
Revises: 0009_group_parent
Create Date: 2026-08-30
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0010_focus_point"
down_revision: Union[str, Sequence[str], None] = "0009_group_parent"
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
    _add_column(bind, "music_videos", "focus_x", "focus_x REAL")
    _add_column(bind, "music_videos", "focus_y", "focus_y REAL")


def downgrade() -> None:
    return
