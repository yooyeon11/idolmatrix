"""艺人/组合详情横幅路径。

Revision ID: 0006_artist_group_banner
Revises: 0005_mtphotos_photo_source
Create Date: 2026-08-25
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0006_artist_group_banner"
down_revision: Union[str, Sequence[str], None] = "0005_mtphotos_photo_source"
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
    _add_column(bind, "artists", "banner_path", "banner_path VARCHAR(500)")
    _add_column(bind, "groups", "banner_path", "banner_path VARCHAR(500)")


def downgrade() -> None:
    return
