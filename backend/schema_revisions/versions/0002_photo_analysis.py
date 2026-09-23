"""照片手动 AI 分析字段。

Revision ID: 0002_photo_analysis
Revises: 0001_schema_compat
Create Date: 2026-08-21
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0002_photo_analysis"
down_revision: Union[str, Sequence[str], None] = "0001_schema_compat"
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
    _add_column_if_missing("photos", "analysis", "analysis JSON")
    _add_column_if_missing(
        "photos", "analysis_prompt_version", "analysis_prompt_version VARCHAR(40)"
    )
    _add_column_if_missing("photos", "analyzed_at", "analyzed_at DATETIME")
    _add_column_if_missing("photos", "analysis_scene", "analysis_scene VARCHAR(40)")
    _add_column_if_missing("photos", "analysis_shot", "analysis_shot VARCHAR(40)")
    _add_column_if_missing(
        "photos", "analysis_shoes_type", "analysis_shoes_type VARCHAR(40)"
    )
    _add_column_if_missing(
        "photos", "analysis_hosiery_present", "analysis_hosiery_present VARCHAR(20)"
    )
    _add_column_if_missing(
        "photos", "analysis_hosiery_type", "analysis_hosiery_type VARCHAR(40)"
    )
    bind = op.get_bind()
    for sql in (
        "CREATE INDEX IF NOT EXISTS ix_photos_analysis_scene ON photos(analysis_scene)",
        "CREATE INDEX IF NOT EXISTS ix_photos_analysis_shoes ON photos(analysis_shoes_type)",
        "CREATE INDEX IF NOT EXISTS ix_photos_analysis_hosiery ON photos(analysis_hosiery_present)",
    ):
        bind.execute(text(sql))


def downgrade() -> None:
    pass
