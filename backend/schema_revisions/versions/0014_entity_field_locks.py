"""实体字段锁：entity_field_locks。

Revision ID: 0014_entity_field_locks
Revises: 0013_incoming_files
Create Date: 2026-09-02
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0014_entity_field_locks"
down_revision: Union[str, Sequence[str], None] = "0013_incoming_files"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    # init_db 先 create_all 再跑迁移，表多半已存在，这里只做兜底补齐
    if "entity_field_locks" not in tables:
        bind.execute(
            text(
                """
                CREATE TABLE entity_field_locks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type VARCHAR(16) NOT NULL,
                    entity_id INTEGER NOT NULL,
                    locks JSON,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
    bind.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_field_locks_entity "
            "ON entity_field_locks (entity_type, entity_id)"
        )
    )
    bind.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_entity_field_locks_entity_type "
            "ON entity_field_locks (entity_type)"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "entity_field_locks" in set(insp.get_table_names()):
        bind.execute(text("DROP TABLE entity_field_locks"))
