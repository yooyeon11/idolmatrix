"""频道稳定身份 channel_id：music_videos.source_channel_id + external_channel_videos.channel_id。

背景（业主 2026-10-26）：博主身份原先只有 `original_uploader` 显示名，YouTube 频道改名后
同一频道会被拆成两个博主；缺失检测也会把「已入库但在新名下」的视频误报为缺失。

- `music_videos.source_channel_id`：入库时从 yt-dlp `.info.json` 的 `channel_id` 写入。
- `external_channel_videos.channel_id`：频道快照按 channel_id 归组，同一频道的旧名/新名
  共用一份快照（名称键仅作展示）。

两列都允许 NULL：存量条目先留空，由 `python -m app.cli backfill-channel-ids` 从侧车补齐。

Revision ID: 0024_channel_id
Revises: 0023_download_outputs
Create Date: 2026-10-26
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0024_channel_id"
down_revision: Union[str, Sequence[str], None] = "0023_download_outputs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column(bind, table: str, column: str, ddl: str, index: str | None = None) -> None:
    insp = inspect(bind)
    if table not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns(table)}
    if column not in cols:
        bind.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
    if index:
        existing = {i["name"] for i in inspect(bind).get_indexes(table)}
        if index not in existing:
            bind.execute(text(f"CREATE INDEX IF NOT EXISTS {index} ON {table} ({column})"))


def upgrade() -> None:
    bind = op.get_bind()
    _add_column(
        bind, "music_videos", "source_channel_id", "VARCHAR(64)",
        "ix_music_videos_source_channel_id",
    )
    _add_column(
        bind, "external_channel_videos", "channel_id", "VARCHAR(64)",
        "ix_external_channel_videos_channel_id",
    )


def downgrade() -> None:
    # SQLite 删列不安全（这两列保留无害），回滚只删索引
    bind = op.get_bind()
    for table, index in (
        ("music_videos", "ix_music_videos_source_channel_id"),
        ("external_channel_videos", "ix_external_channel_videos_channel_id"),
    ):
        insp = inspect(bind)
        if table in set(insp.get_table_names()):
            bind.execute(text(f"DROP INDEX IF EXISTS {index}"))
