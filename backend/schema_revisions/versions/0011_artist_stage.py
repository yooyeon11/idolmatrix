"""艺人/组合首页主舞台：一句话简介、头像/横幅焦点、可选 logo。

Revision ID: 0011_artist_stage
Revises: 0010_focus_point
Create Date: 2026-08-30
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0011_artist_stage"
down_revision: Union[str, Sequence[str], None] = "0010_focus_point"
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
    for table in ("artists", "groups"):
        _add_column(bind, table, "tagline", "tagline VARCHAR(500)")
        _add_column(bind, table, "focus_x", "focus_x REAL")
        _add_column(bind, table, "focus_y", "focus_y REAL")
        _add_column(bind, table, "logo_path", "logo_path VARCHAR(500)")


def downgrade() -> None:
    return
