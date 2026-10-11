"""external_channel_videos：博主频道投稿快照（YouTube 缺失检测）。

Revision ID: 0016_external_channel_videos
Revises: 0015_hide_from_home
Create Date: 2026-09-27
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0016_external_channel_videos"
down_revision: Union[str, Sequence[str], None] = "0015_hide_from_home"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "external_channel_videos"


def upgrade() -> None:
    # 全新库上 create_all 已建好本表；这里只为存量库补建，先探测避免重复建表报错
    bind = op.get_bind()
    insp = inspect(bind)
    if _TABLE in set(insp.get_table_names()):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("uploader_key", sa.String(200), nullable=False),
        sa.Column("video_id", sa.String(50), nullable=False),
        sa.Column("title", sa.String(500), nullable=False, server_default=""),
        sa.Column("thumbnail_path", sa.String(500), nullable=True),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("is_short", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "last_seen_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "uploader_key", "video_id", name="uq_external_channel_video"
        ),
    )
    op.create_index(
        "ix_external_channel_videos_uploader",
        _TABLE,
        ["uploader_key"],
    )
    op.create_index(
        "ix_external_channel_videos_video_id",
        _TABLE,
        ["video_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if _TABLE not in set(insp.get_table_names()):
        return
    op.drop_index("ix_external_channel_videos_video_id", table_name=_TABLE)
    op.drop_index("ix_external_channel_videos_uploader", table_name=_TABLE)
    op.drop_table(_TABLE)
