"""下载任务：落盘明细 + 字幕开关

Revision ID: 0023_download_outputs
Revises: 0022_download_jobs
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023_download_outputs"
down_revision: Union[str, Sequence[str], None] = "0022_download_jobs"
branch_labels = None
depends_on = None


def _cols() -> set[str]:
    insp = sa.inspect(op.get_bind())
    return {c["name"] for c in insp.get_columns("download_jobs")}


def upgrade() -> None:
    # 新库由 create_all 先建表，列可能已存在 —— 幂等处理
    cols = _cols()
    if "outputs" not in cols:
        op.add_column("download_jobs", sa.Column("outputs", sa.Text(), nullable=True))
    if "download_subtitles" not in cols:
        op.add_column(
            "download_jobs",
            sa.Column("download_subtitles", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    cols = _cols()
    if "download_subtitles" in cols:
        op.drop_column("download_jobs", "download_subtitles")
    if "outputs" in cols:
        op.drop_column("download_jobs", "outputs")
