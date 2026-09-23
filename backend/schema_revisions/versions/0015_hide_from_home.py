"""artists/groups.hide_from_home：首页主舞台排除开关。

Revision ID: 0015_hide_from_home
Revises: 0014_entity_field_locks
Create Date: 2026-09-08
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0015_hide_from_home"
down_revision: Union[str, Sequence[str], None] = "0014_entity_field_locks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_bool_col(table: str, col: str) -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if table not in set(insp.get_table_names()):
        return
    existing = {c["name"] for c in insp.get_columns(table)}
    if col in existing:
        return
    bind.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} BOOLEAN NOT NULL DEFAULT 0"))


def upgrade() -> None:
    _add_bool_col("artists", "hide_from_home")
    _add_bool_col("groups", "hide_from_home")


def downgrade() -> None:
    # SQLite 不便安全删列；保留列无害
    pass
