"""YouTube 下载：下载任务 / 收藏夹订阅 / 订阅去重台账。

三张新表：
- `download_jobs`             单次下载任务（单视频或整个播放列表）
- `download_subscriptions`    订阅的 YouTube 收藏夹（播放列表）
- `download_subscription_items` 订阅里出现过的视频（防重复入队）

Revision ID: 0022_download_jobs
Revises: 0021_video_camera_session
Create Date: 2026-10-18
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, inspect

revision: str = "0022_download_jobs"
down_revision: Union[str, Sequence[str], None] = "0021_video_camera_session"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return name in inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("download_subscriptions"):
        op.create_table(
            "download_subscriptions",
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("url", String(1000), nullable=False),
            Column("name", String(300)),
            Column("enabled", Integer, nullable=False, server_default="1"),
            Column("download_existing", Integer, nullable=False, server_default="0"),
            Column("initialized", Integer, nullable=False, server_default="0"),
            Column("last_checked_at", DateTime),
            Column("last_error", Text),
            Column("total_enqueued", Integer, nullable=False, server_default="0"),
            Column("created_at", DateTime),
            Column("updated_at", DateTime),
        )

    if not _has_table("download_jobs"):
        op.create_table(
            "download_jobs",
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("url", String(1000), nullable=False),
            Column("video_id", String(64)),
            Column("mode", String(16), nullable=False, server_default="video"),
            Column("title", String(300)),
            Column("uploader", String(200)),
            Column("duration", Integer),
            Column("status", String(20), nullable=False, server_default="queued"),
            Column("progress", Float, nullable=False, server_default="0"),
            Column("downloaded_bytes", Integer),
            Column("total_bytes", Integer),
            Column("speed", String(32)),
            Column("eta", String(16)),
            Column("error", Text),
            Column("output_path", String(2000)),
            Column(
                "subscription_id",
                Integer,
                ForeignKey("download_subscriptions.id", ondelete="SET NULL"),
            ),
            Column("started_at", DateTime),
            Column("finished_at", DateTime),
            Column("created_at", DateTime),
            Column("updated_at", DateTime),
        )
        op.create_index("ix_download_jobs_status", "download_jobs", ["status"])
        op.create_index("ix_download_jobs_video_id", "download_jobs", ["video_id"])
        op.create_index("ix_download_jobs_subscription_id", "download_jobs", ["subscription_id"])

    if not _has_table("download_subscription_items"):
        op.create_table(
            "download_subscription_items",
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column(
                "subscription_id",
                Integer,
                ForeignKey("download_subscriptions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            Column("video_id", String(64), nullable=False),
            Column("title", String(300)),
            Column("state", String(16), nullable=False, server_default="seen"),
            Column("job_id", Integer),
            Column("created_at", DateTime),
            Column("updated_at", DateTime),
        )
        op.create_index(
            "ix_download_subscription_items_subscription_id",
            "download_subscription_items",
            ["subscription_id"],
        )
        op.create_index(
            "ix_download_subscription_items_video_id",
            "download_subscription_items",
            ["video_id"],
        )


def downgrade() -> None:
    op.drop_table("download_subscription_items")
    op.drop_table("download_jobs")
    op.drop_table("download_subscriptions")
