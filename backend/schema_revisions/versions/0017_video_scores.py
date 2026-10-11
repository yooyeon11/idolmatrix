"""video_scores 迁移：AI 视频评分表（实验功能）。

Revision ID: 0017_video_scores
Revises: 0016_external_channel_videos
Create Date: 2026-10-02
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0017_video_scores"
down_revision: Union[str, Sequence[str], None] = "0016_external_channel_videos"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "video_scores"


def upgrade() -> None:
    # 全新库上 create_all 已建好本表；这里只为存量库补建，先探测避免重复建表报错
    bind = op.get_bind()
    insp = inspect(bind)
    if _TABLE in set(insp.get_table_names()):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "music_video_id",
            sa.Integer(),
            sa.ForeignKey("music_videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.String(200), nullable=False, server_default=""),
        sa.Column("dims", sa.JSON(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("frames", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scored_at", sa.DateTime(), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_video_scores_mv", _TABLE, ["music_video_id", "created_at"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if _TABLE not in set(insp.get_table_names()):
        return
    op.drop_index("ix_video_scores_mv", table_name=_TABLE)
    op.drop_table(_TABLE)
