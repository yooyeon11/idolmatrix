"""ApiToken + AuditLog：外部集成的分级门禁与操作审计。

背景（业主 2026-11-13）：AI/脚本要能管艺人、组合、视频与入库，但现状
只有一张 `app_settings.external_api.token` —— 无分级、无撤销、无使用记录，
出了事查不到是谁改的。本迁移加两张表：

- `api_tokens`：多张卡并存，各自带 scope / 有效期 / 撤销 / 最后使用时间
- `audit_logs`：只记写操作，只存字段级 diff（不存整对象，避免表比业务表还大）

旧 token 不做迁移：`deps.get_external_principal` 保留兼容回退，
签发新卡后可在设置页清空旧值。

Revision ID: 0026_api_tokens_audit
Revises: 0025_video_publish
Create Date: 2026-11-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0026_api_tokens_audit"
down_revision: Union[str, Sequence[str], None] = "0025_video_publish"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())

    # 全新库由 init_db() 的 create_all 先建表，这里只为存量库兜底；
    # 两边 DDL 必须与模型定义保持一致。
    if "api_tokens" not in tables:
        bind.execute(
            text(
                """
                CREATE TABLE api_tokens (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
                    updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
                    name VARCHAR(80) NOT NULL,
                    token_hash VARCHAR(64) NOT NULL,
                    token_hint VARCHAR(16) NOT NULL DEFAULT '',
                    scopes TEXT NOT NULL DEFAULT '["read"]',
                    expires_at DATETIME,
                    revoked_at DATETIME,
                    last_used_at DATETIME,
                    last_used_ip VARCHAR(64),
                    created_by VARCHAR(80),
                    note VARCHAR(200)
                )
                """
            )
        )
        bind.execute(text("CREATE UNIQUE INDEX ix_api_tokens_name ON api_tokens (name)"))
        bind.execute(
            text("CREATE UNIQUE INDEX ix_api_tokens_token_hash ON api_tokens (token_hash)")
        )

    if "audit_logs" not in tables:
        bind.execute(
            text(
                """
                CREATE TABLE audit_logs (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    request_id VARCHAR(36) NOT NULL DEFAULT '',
                    actor_type VARCHAR(12) NOT NULL DEFAULT 'web',
                    actor_label VARCHAR(80) NOT NULL DEFAULT '',
                    method VARCHAR(8) NOT NULL,
                    path VARCHAR(300) NOT NULL,
                    entity_type VARCHAR(40),
                    entity_id INTEGER,
                    action VARCHAR(16) NOT NULL DEFAULT 'other',
                    status_code INTEGER NOT NULL DEFAULT 0,
                    changes_json TEXT,
                    ip VARCHAR(64),
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL
                )
                """
            )
        )
        bind.execute(
            text("CREATE INDEX ix_audit_logs_request_id ON audit_logs (request_id)")
        )
        bind.execute(
            text("CREATE INDEX ix_audit_logs_created_at ON audit_logs (created_at)")
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "audit_logs" in tables:
        bind.execute(text("DROP TABLE audit_logs"))
    if "api_tokens" in tables:
        bind.execute(text("DROP TABLE api_tokens"))
