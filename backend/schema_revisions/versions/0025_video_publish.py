"""VideoPublish：视频在外部平台（B 站）的分发记录。

背景（业主 2026-11-08）：要做在线版，本地上传/入库的视频需要在云端也能播放。
播放源不走自建存储，而是 B 站 iframe —— 因为 K-pop 舞台在 B 站大概率
已有同一条搬运，复用它零成本、零风控、零画质损失。

本表只管「这个视频对应哪个 BV 号」和「这个 BV 号现在还能不能播」，
不参与本地播放决策（本地有文件时永远优先本地）。

Revision ID: 0025_video_publish
Revises: 0024_channel_id
Create Date: 2026-11-08
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0025_video_publish"
down_revision: Union[str, Sequence[str], None] = "0024_channel_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "video_publishes" not in set(insp.get_table_names()):
        # 注意：init_db() 的 create_all 通常已建好表，这里是为「版本表停在
        # 0024 但表缺失」的旧库兜底；两边 DDL 必须保持一致。
        bind.execute(
            text(
                """
                CREATE TABLE video_publishes (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    uid VARCHAR(36) NOT NULL,
                    deleted_at DATETIME,
                    created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
                    updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
                    music_video_id INTEGER NOT NULL,
                    platform VARCHAR(20) NOT NULL,
                    bvid VARCHAR(20),
                    page INTEGER NOT NULL,
                    origin VARCHAR(20) NOT NULL,
                    owned_by_me BOOLEAN DEFAULT 0 NOT NULL,
                    status VARCHAR(20) NOT NULL,
                    match_score FLOAT,
                    match_note VARCHAR(300),
                    source_url VARCHAR(500),
                    submitted_at DATETIME,
                    last_checked_at DATETIME,
                    error TEXT,
                    FOREIGN KEY(music_video_id) REFERENCES music_videos (id)
                        ON DELETE CASCADE
                )
                """
            )
        )

    # 索引：唯一约束必须带 deleted_at IS NULL（软删除行要让位给重新绑定）
    stmts = (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_video_publishes_active "
        "ON video_publishes (music_video_id, platform) "
        "WHERE deleted_at IS NULL",
        "CREATE INDEX IF NOT EXISTS ix_video_publishes_active "
        "ON video_publishes (status, deleted_at)",
        "CREATE INDEX IF NOT EXISTS ix_video_publishes_music_video_id "
        "ON video_publishes (music_video_id)",
        "CREATE INDEX IF NOT EXISTS ix_video_publishes_bvid "
        "ON video_publishes (bvid)",
        "CREATE INDEX IF NOT EXISTS ix_video_publishes_uid "
        "ON video_publishes (uid)",
        "CREATE INDEX IF NOT EXISTS ix_video_publishes_deleted_at "
        "ON video_publishes (deleted_at)",
    )
    for stmt in stmts:
        bind.execute(text(stmt))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "video_publishes" in set(insp.get_table_names()):
        bind.execute(text("DROP TABLE IF EXISTS video_publishes"))
