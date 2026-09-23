"""组合自引用上级：小分队归属完整体。

Revision ID: 0009_group_parent
Revises: 0008_cover_manual
Create Date: 2026-08-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0009_group_parent"
down_revision: Union[str, Sequence[str], None] = "0008_cover_manual"
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
    _add_column(bind, "groups", "parent_group_id", "parent_group_id INTEGER")
    # SQLite ALTER 无法附带 FK 约束与索引，索引由启动补丁/建表保证；此处补建索引（幂等）
    insp = inspect(bind)
    idx = {i["name"] for i in insp.get_indexes("groups")}
    if "ix_groups_parent_group_id" not in idx:
        bind.execute(
            text("CREATE INDEX ix_groups_parent_group_id ON groups (parent_group_id)")
        )


def downgrade() -> None:
    return
