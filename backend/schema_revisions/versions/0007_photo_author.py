"""照片发布者字段（粉丝/官方帖子按发布者筛选与搜索）。

Revision ID: 0007_photo_author
Revises: 0006_artist_group_banner
Create Date: 2026-08-26
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0007_photo_author"
down_revision: Union[str, Sequence[str], None] = "0006_artist_group_banner"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_if_missing(table: str, column: str, ddl: str) -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if table not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns(table)}
    if column in existing:
        return
    bind.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))


def upgrade() -> None:
    _add_column_if_missing("photos", "author", "author VARCHAR(120)")
    bind = op.get_bind()
    bind.execute(
        text("CREATE INDEX IF NOT EXISTS ix_photos_author ON photos(author)")
    )


def downgrade() -> None:
    pass
