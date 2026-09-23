"""登录门禁：users + auth_sessions。

Revision ID: 0012_auth
Revises: 0011_artist_stage
Create Date: 2026-08-31
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0012_auth"
down_revision: Union[str, Sequence[str], None] = "0011_artist_stage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "users" not in tables:
        bind.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid VARCHAR(36) NOT NULL,
                    deleted_at DATETIME,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    username VARCHAR(32) NOT NULL,
                    display_name VARCHAR(80) NOT NULL DEFAULT '',
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(20) NOT NULL DEFAULT 'owner',
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    last_login_at DATETIME
                )
                """
            )
        )
    bind.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_uid ON users (uid)"
        )
    )
    bind.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_username_ci "
            "ON users (username COLLATE NOCASE) WHERE deleted_at IS NULL"
        )
    )
    if "auth_sessions" not in tables:
        bind.execute(
            text(
                """
                CREATE TABLE auth_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token_hash VARCHAR(64) NOT NULL,
                    user_agent VARCHAR(400),
                    ip VARCHAR(64),
                    created_at DATETIME NOT NULL,
                    last_seen_at DATETIME NOT NULL,
                    expires_at DATETIME NOT NULL,
                    revoked_at DATETIME
                )
                """
            )
        )
    bind.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_auth_sessions_token_hash "
            "ON auth_sessions (token_hash)"
        )
    )
    bind.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_auth_sessions_user_id ON auth_sessions (user_id)"
        )
    )


def downgrade() -> None:
    return
