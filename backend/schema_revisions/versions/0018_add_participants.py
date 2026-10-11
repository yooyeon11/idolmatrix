"""music_videos 增加 participants 列：视频参与者（仅详情页展示，无其它数据影响）。

Revision ID: 0018_add_participants
Revises: 0017_video_scores
Create Date: 2026-10-03
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0018_add_participants"
down_revision: Union[str, Sequence[str], None] = "0017_video_scores"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "music_videos"
_COL = "participants"


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns(_TABLE)}
    if _COL in cols:
        return
    # JSON 列:存 [{id?: int, name: str}] —— id 为库内艺人 id（展示头像/详情跳转），
    # name 为选择时的名字快照。除详情页展示外无任何消费方。
    op.add_column(_TABLE, sa.Column(_COL, sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns(_TABLE)}
    if _COL not in cols:
        return
    op.drop_column(_TABLE, _COL)
