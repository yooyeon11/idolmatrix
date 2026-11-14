"""artists/groups.featured_in_home：首页主舞台轮动（opt-in）。

旧字段 hide_from_home 是「排除式」开关（默认全员参与，勾选才排除）；
本次翻转为「加入式」：默认不参与，只有 featured_in_home=1 才进轮动池。
升级时按旧值取反回填，保证升级前后首页展示的集合不变。

Revision ID: 0020_featured_in_home
Revises: 0019_incoming_source_id
Create Date: 2026-10-16
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0020_featured_in_home"
down_revision: Union[str, Sequence[str], None] = "0019_incoming_source_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("artists", "groups")


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    for table in _TABLES:
        if table not in tables:
            continue
        cols = {c["name"] for c in insp.get_columns(table)}
        if "featured_in_home" not in cols:
            bind.execute(
                text(
                    f"ALTER TABLE {table} ADD COLUMN featured_in_home "
                    "BOOLEAN NOT NULL DEFAULT 0"
                )
            )
        # 取反回填：旧「未排除」= 新「已加入」。全新库此时无 hide_from_home
        # 列（表为空），跳过即可，让模型默认值 False 生效。
        if "hide_from_home" in cols:
            bind.execute(
                text(
                    f"UPDATE {table} SET featured_in_home = "
                    "CASE WHEN hide_from_home = 1 THEN 0 ELSE 1 END"
                )
            )


def downgrade() -> None:
    # 旧列 hide_from_home 未删除（SQLite 删列不安全，且保留无害）；
    # 回滚时按新值取反写回，旧版本首页集合保持一致。
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    for table in _TABLES:
        if table not in tables:
            continue
        cols = {c["name"] for c in insp.get_columns(table)}
        if "featured_in_home" in cols and "hide_from_home" in cols:
            bind.execute(
                text(
                    f"UPDATE {table} SET hide_from_home = "
                    "CASE WHEN featured_in_home = 1 THEN 0 ELSE 1 END"
                )
            )
