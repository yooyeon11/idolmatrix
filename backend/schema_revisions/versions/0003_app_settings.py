"""跨设备应用设置表。

Revision ID: 0003_app_settings
Revises: 0002_photo_analysis
Create Date: 2026-08-22
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0003_app_settings"
down_revision: Union[str, Sequence[str], None] = "0002_photo_analysis"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "app_settings" in insp.get_table_names():
        return
    bind.execute(
        text(
            "CREATE TABLE app_settings ("
            "key VARCHAR(40) NOT NULL PRIMARY KEY, "
            "value JSON, "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "app_settings" in insp.get_table_names():
        bind.execute(text("DROP TABLE app_settings"))
