"""待整理扫描落库：incoming_files。

Revision ID: 0013_incoming_files
Revises: 0012_auth
Create Date: 2026-09-01
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0013_incoming_files"
down_revision: Union[str, Sequence[str], None] = "0012_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    # init_db 先 create_all 再跑迁移，表多半已存在，这里只做兜底补齐
    if "incoming_files" not in tables:
        bind.execute(
            text(
                """
                CREATE TABLE incoming_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path VARCHAR(1000) NOT NULL,
                    file_name VARCHAR(500) NOT NULL,
                    file_size INTEGER,
                    mtime REAL,
                    duration INTEGER,
                    width INTEGER,
                    height INTEGER,
                    video_codec VARCHAR(50),
                    audio_codec VARCHAR(50),
                    is_duplicate BOOLEAN NOT NULL DEFAULT 0,
                    probed_at DATETIME,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
    bind.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_incoming_files_path "
            "ON incoming_files (file_path)"
        )
    )
    bind.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_incoming_files_file_name "
            "ON incoming_files (file_name)"
        )
    )


def downgrade() -> None:
    return
