"""incoming_files 增加平台视频身份列：source_platform / source_id。

待整理列表的「已存在」提示从「文件名+大小 / 大小+时长」扩为优先按
平台视频 ID 判重 —— 换画质 / 换封装重下载（大小时长都变）也能命中。

Revision ID: 0019_incoming_source_id
Revises: 0018_add_participants
Create Date: 2026-10-11
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0019_incoming_source_id"
down_revision: Union[str, Sequence[str], None] = "0018_add_participants"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "incoming_files"
_COLS = ("source_platform", "source_id")


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns(_TABLE)}
    if "source_platform" not in cols:
        op.add_column(_TABLE, sa.Column("source_platform", sa.String(50), nullable=True))
    if "source_id" not in cols:
        op.add_column(_TABLE, sa.Column("source_id", sa.String(200), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns(_TABLE)}
    if "source_id" in cols:
        op.drop_column(_TABLE, "source_id")
    if "source_platform" in cols:
        op.drop_column(_TABLE, "source_platform")
